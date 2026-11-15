"""Inspect one synthetic Firehose Parquet locally or relay it using the operator's own AWS/GCP access."""

import argparse
import io
import json
import os
import re
import sys
from datetime import UTC, date
from pathlib import Path

import boto3
import google.auth
import pyarrow.parquet as pq
from google.cloud import bigquery
from google.oauth2.credentials import Credentials as UserCredentials

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root), str(root / "shared")]

from services.bigquery_sync_back.handler import return_selected_kpi, validate_scope  # noqa: E402
from services.lake_to_bigquery.loader import existing_messages, load_manifest  # noqa: E402
from services.lake_to_bigquery.parquet_input import import_parquet  # noqa: E402
from services.lake_to_bigquery.telemetry import bigquery_row, telemetry_row  # noqa: E402
from services.stream_exporter.lake_io import MAX_FILE, PENDING, DataError, canonical, digest, entry, verified_archive  # noqa: E402

REGION = "us-east-1"
PROJECT = "arquifusion"
DATASET = "af_bridge_stage"


def inspect_file(content, expected):
    if len(content) > MAX_FILE:
        raise DataError("File exceeds the bridge size limit")
    source = pq.ParquetFile(io.BytesIO(content), coerce_int96_timestamp_unit="us")
    if (
        source.metadata.num_rows != 1
        or sum(source.metadata.row_group(i).total_byte_size for i in range(source.num_row_groups)) > MAX_FILE
    ):
        raise DataError("The operator rehearsal requires one bounded synthetic reading")
    [physical] = source.read().to_pylist()
    timestamp = next((k for k in physical if k.lower() == "timestamp"), None)
    if timestamp and source.schema.column(source.schema.names.index(timestamp)).physical_type == "INT96":
        # The Firehose fixture uses an unzoned physical INT96 for the contract's UTC timestamp.
        instant = physical[timestamp]
        if instant is not None and instant.tzinfo is None:
            physical[timestamp] = instant.replace(tzinfo=UTC)
    row = bigquery_row(physical)
    if row != telemetry_row(expected):
        raise DataError("Parquet differs from the full original simulator message")
    validate_scope(row["machineId"], row["operationId"], date.fromisoformat(row["timestamp"][:10]))
    return row


def run_remote(action, archive, table, client, key, content, row):
    if client.project != PROJECT or not re.fullmatch(
        r"records/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{2}/[\w.-]+\.parquet", key
    ):
        raise DataError("Expected one Firehose records object in the ArquiFusion project")
    etag = archive.client.head_object(**archive.args(key))["ETag"]
    if archive.read(key, etag=etag) != content:
        raise DataError("S3 object differs from the reviewed file")
    destination = PROJECT + ".af_lake.telemetry"
    if action == "load":
        receipt = import_parquet(archive, client, {"Key": key, "ETag": etag}, DATASET)
        if receipt["rows"] != 1 or receipt["sourceSha256"] != digest(content):
            raise DataError("Unexpected staging receipt")
        # This single-row manifest uses the same canonical bytes as Archive.publish.
        source = digest(canonical([archive.bucket, key, etag]))
        fact = entry(row["messageId"], source, row)
        batch = digest(canonical(fact) + b"\n")
        final = load_manifest(archive, client, f"{PENDING}telemetry/{batch}.json")
        result = {"stageJob": receipt["stageJob"], "loadJob": final["job"], "batch": batch}
    elif action == "return-kpi":
        result = {}
    else:
        raise DataError("Unknown relay action")
    if existing_messages(client, destination, [row], timeout=30) != [row["messageId"]]:
        raise DataError("Expected exactly one matching source message in BigQuery")
    if action == "return-kpi":
        result.update(
            return_selected_kpi(
                client,
                table,
                machine_id=row["machineId"],
                operation_id=row["operationId"],
                kpi_date=date.fromisoformat(row["timestamp"][:10]),
            )
        )
    return result


def operator_clients(account):
    if not account or not re.fullmatch(r"[0-9]{12}", account) or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise DataError("Use the approved Academy account and a personal Google ADC session")
    session = boto3.Session(profile_name="default", region_name=REGION)
    if session.client("sts").get_caller_identity()["Account"] != account:
        raise DataError("AWS session does not match the approved Academy account")
    archive = verified_archive(session.client("s3"), f"af-dev-arquifusion-lake-{account}-{REGION}-an", account)
    table = session.resource("dynamodb").Table("af-dev-arquifusion")
    if table.table_arn != f"arn:aws:dynamodb:{REGION}:{account}:table/af-dev-arquifusion":
        raise DataError("Unexpected operational table")
    credential, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"], quota_project_id=PROJECT)
    if not isinstance(credential, UserCredentials):
        raise DataError("This manual relay requires a human Google account authorized for the project")
    return archive, table, bigquery.Client(project=PROJECT, location="US", credentials=credential)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "load", "return-kpi"])
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--reading", required=True, type=Path, help="Original simulator JSON")
    parser.add_argument("--account", help="Approved Academy account for a cloud stage")
    parser.add_argument("--object", help="Exact lake key under records/YYYY/MM/DD/HH/")
    args = parser.parse_args()
    if args.parquet.stat().st_size > MAX_FILE:
        parser.error("File exceeds the bridge size limit")
    content = args.parquet.read_bytes()
    row = inspect_file(content, json.loads(args.reading.read_text(encoding="utf-8")))
    info = {
        "action": args.action,
        "sha256": digest(content),
        "messageId": row["messageId"],
        "machineId": row["machineId"],
    }
    if args.action != "inspect":
        if not args.account or not args.object:
            parser.error("Cloud stages require --account and --object")
        info.update(run_remote(args.action, *operator_clients(args.account), args.object, content, row))
    print(json.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
