"""Load original Firehose bytes into an expiring staging table and enqueue validated telemetry facts."""

import re
from datetime import UTC, datetime

from google.cloud import bigquery

from services.lake_to_bigquery.load_jobs import load_file
from services.lake_to_bigquery.telemetry import bigquery_row
from services.stream_exporter.lake_io import CHUNK, MAX_FILE, ROOT, DataError, canonical, digest, entry

MAX_ROWS = 50_000


def staging_window(client, dataset):
    if (
        not dataset
        or not re.fullmatch(r"[a-zA-Z0-9_]{1,1024}", dataset)
        or dataset.lower() in {"af_lake", "af_ml", "af_out"}
    ):
        raise DataError("configure a dedicated BigQuery staging dataset")
    metadata = client.get_dataset(f"{client.project}.{dataset}")
    ttl = metadata.default_table_expiration_ms
    if metadata.location != "US" or not ttl or ttl <= 0:
        raise DataError("staging requires a US dataset with a default table expiration")
    # Each window ends before a table created in it can expire. A later retry can restage
    # a retained S3 object even after BigQuery expired the previous table and kept its job.
    return min(ttl, 86_400_000)


def import_parquet(archive, client, obj, dataset, *, now=None):
    key = obj["Key"]
    if not key.lower().endswith(".parquet"):
        return
    stamp = digest(canonical([archive.bucket, key, obj["ETag"]]))
    seen = f"{ROOT}control/{client.project}/parquet/{stamp}.json"
    if receipt := archive.json(seen):
        return receipt
    content = archive.read(key, etag=obj["ETag"])
    if content is None:
        raise DataError("listed Parquet object disappeared")
    if len(content) < 12 or content[:4] != b"PAR1" or content[-4:] != b"PAR1":
        raise DataError("invalid Parquet envelope")
    window = staging_window(client, dataset)
    instant = now or datetime.now(UTC)
    generation = int(instant.timestamp() * 1000) // window
    destination = f"{client.project}.{dataset}.telemetry_{stamp}_{generation}"
    source_sha = digest(content)
    job_hash = digest(canonical(["parquet", destination, source_sha]))
    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
        max_bad_records=0,
    )
    job = load_file(archive, client, destination, content, config, job_hash)
    if job.output_rows > MAX_ROWS:
        raise DataError("split large Parquet files before intake")

    entries = []
    identities = {}
    total_bytes = 0
    for record in client.list_rows(destination, page_size=CHUNK):
        row = bigquery_row(record)
        encoded = canonical(row)
        total_bytes += len(encoded)
        if total_bytes > MAX_FILE or len(entries) >= MAX_ROWS:
            raise DataError("decoded telemetry exceeds bridge limit")
        row_sha = digest(encoded)
        message_id = row["messageId"]
        if identities.setdefault(message_id, row_sha) != row_sha:
            raise DataError("conflicting revisions of the same Parquet messageId")
        entries.append(entry(message_id, stamp, row))
    if len(entries) != job.output_rows:
        raise DataError("staging row count mismatch")
    # tabledata.list has no ordering guarantee. A retry must publish the same immutable chunks.
    entries.sort(key=lambda item: item["id"])
    for offset in range(0, len(entries), CHUNK):
        archive.publish("telemetry", entries[offset : offset + CHUNK])
    receipt = {"sourceSha256": source_sha, "rows": len(entries), "stageJob": job.job_id}
    if not archive.put_new(seen, canonical(receipt)):
        saved = archive.json(seen)
        if not saved or saved.get("sourceSha256") != source_sha or saved.get("rows") != len(entries):
            raise DataError("Parquet staging receipt conflict")
        return saved
    return receipt
