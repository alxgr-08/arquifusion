"""Discover the configured Parquet prefix and drain pending ArquiFusion manifests in bounded pages."""

import logging
import os
import re
from functools import cache

from google.auth import aws
from google.cloud import bigquery

from services.lake_to_bigquery.loader import load_manifest
from services.lake_to_bigquery.parquet_input import import_parquet
from services.stream_exporter.lake_io import PENDING, ROOT, DataError, canonical, configured_archive, digest

LOG = logging.getLogger(__name__)


def walk(archive, prefix, cursor_key, process, limit, remaining):
    # Cursors are scan hints. Work is committed by per-object receipts; a concurrent cursor update
    # can cause a repeated scan but cannot acknowledge or lose an object. Wrap for late or failed keys.
    cursor = archive.json(cursor_key) or {}
    start = cursor.get("after", "")
    objects, more = archive.page(prefix, start, limit)
    failures, visited, last = [], 0, start
    for obj in objects:
        if remaining() < 18:
            more = True
            break
        try:
            process(obj)
        except Exception as exc:
            reference = digest(obj["Key"].encode())[:16]
            failures.append(reference)
            detail = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            LOG.error("Bridge object %s remains pending (%s)", reference, detail)
        visited += 1
        last = obj["Key"]
    after = last if more else ""
    if start != after:
        archive.client.put_object(
            **archive.args(cursor_key), Body=canonical({"after": after}), ContentType="application/json"
        )
    return {"visited": visited, "more": more, "failures": failures}


def run(archive, client, *, parquet_prefix=None, staging_dataset=None, limit=20, remaining=lambda: float("inf")):
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", client.project):
        raise DataError("invalid ArquiFusion GCP project ID")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DataError("bridge page size must be 1 to 100")
    cursors = f"{ROOT}control/{client.project}/cursors/"
    results = {}
    if parquet_prefix:
        if not staging_dataset:
            raise DataError("AF_BQ_STAGING_DATASET is required for Parquet input")
        if (
            not parquet_prefix.endswith("/")
            or parquet_prefix.startswith("/")
            or ".." in parquet_prefix
            or ROOT.startswith(parquet_prefix)
            or parquet_prefix.startswith(ROOT)
        ):
            raise DataError("Parquet input prefix must be separate from the bridge archive")
        results["parquet"] = walk(
            archive,
            parquet_prefix,
            cursors + "parquet.json",
            lambda obj: import_parquet(archive, client, obj, staging_dataset),
            limit,
            remaining,
        )
    results["pending"] = walk(
        archive,
        PENDING,
        cursors + "pending.json",
        lambda obj: load_manifest(archive, client, obj["Key"]),
        limit,
        remaining,
    )
    return results


@cache
def bigquery_client():
    descriptor = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if descriptor != "/opt/arquifusion/gcp-wif.json" or os.getenv("GCP_PROJECT_ID") != "arquifusion":
        raise DataError("configure the approved ArquiFusion AWS federation layer and project")
    credentials = aws.Credentials.from_file(descriptor, scopes=["https://www.googleapis.com/auth/bigquery"])
    return bigquery.Client(project="arquifusion", location="US", credentials=credentials)


def handler(event, context):
    archive = configured_archive()
    if "replay" in event:
        replay = event["replay"]
        archive.requeue(replay["kind"], replay["batch"])
    result = run(
        archive,
        bigquery_client(),
        parquet_prefix=os.getenv("AF_PARQUET_PREFIX"),
        staging_dataset=os.getenv("AF_BQ_STAGING_DATASET"),
        limit=int(os.getenv("AF_LAKE_PAGE_SIZE", "20")),
        remaining=lambda: context.get_remaining_time_in_millis() / 1000,
    )
    if any(page["failures"] for page in result.values()):
        raise RuntimeError("Some bridge inputs remain pending; inspect the sanitized object references")
    return result
