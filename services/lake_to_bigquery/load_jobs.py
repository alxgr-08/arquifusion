"""Recover an atomic BigQuery batch load by the job ID recorded before submission."""

import io
from datetime import UTC, datetime, timedelta

from google.api_core.exceptions import Conflict, NotFound
from google.cloud import bigquery

from services.stream_exporter.lake_io import ROOT, DataError, canonical

RETRYABLE = {
    "backendError",
    "internalError",
    "jobBackendError",
    "jobInternalError",
    "jobRateLimitExceeded",
    "rateLimitExceeded",
}


def load_attempt(client, destination, payload, config, job_id, opened_at, *, now, timeout, expected_rows=None):
    try:
        job = client.get_job(job_id, project=client.project, location="US")
    except NotFound:
        if now - opened_at > timedelta(days=150):
            raise DataError("old load intent needs reconciliation") from None
        try:
            job = client.load_table_from_file(
                io.BytesIO(payload), destination, size=len(payload), job_config=config, job_id=job_id, location="US"
            )
        except Conflict:
            job = client.get_job(job_id, project=client.project, location="US")
    if (
        job.job_type != "load"
        or job.job_id != job_id
        or job.project != client.project
        or job.location != "US"
        or str(job.destination) != destination
        or job.write_disposition != config.write_disposition
        or job.create_disposition != config.create_disposition
        or job.source_format != config.source_format
    ):
        raise DataError("unexpected job configuration")
    try:
        job.result(timeout=timeout)
    except Exception:
        job = client.get_job(job_id, project=client.project, location="US")
        if job.state != "DONE":
            raise
    if job.state != "DONE":
        raise DataError("load completion unverified")
    if job.error_result:
        if job.error_result.get("reason") in RETRYABLE:
            return None
        raise DataError("terminal BigQuery load error")
    if (
        type(job.output_rows) is not int
        or job.output_rows < 0
        or (expected_rows is not None and job.output_rows != expected_rows)
    ):
        raise DataError("committed row count unverified")
    return job


def load_file(archive, client, destination, payload, config, job_hash, *, expected_rows=None, timeout=10):
    path = f"{ROOT}control/{client.project}/jobs/{job_hash}"
    now = datetime.now(UTC)
    intent = {"openedAt": now.isoformat()}
    if not archive.put_new(path + ".json", canonical(intent)):
        intent = archive.json(path + ".json")
    retry_key = path + ".retry.json"
    progress = archive.json(retry_key)
    first = progress["next"] if progress else 0
    if type(first) is not int or first < 0:
        raise DataError("invalid job retry cursor")
    for attempt in range(first, first + 3):
        job = load_attempt(
            client,
            destination,
            payload,
            config,
            f"af_lake_{job_hash}_{attempt}",
            datetime.fromisoformat(intent["openedAt"]),
            now=now,
            timeout=timeout,
            expected_rows=expected_rows,
        )
        if job is not None:
            return job
    # A stale concurrent cursor merely rechecks jobs that have already failed atomically.
    archive.client.put_object(
        **archive.args(retry_key), Body=canonical({"next": first + 3}), ContentType="application/json"
    )
    raise DataError("retryable BigQuery jobs failed; batch remains pending")


def append_config():
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        autodetect=False,
        ignore_unknown_values=False,
        max_bad_records=0,
    )
