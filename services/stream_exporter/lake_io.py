"""Private S3 exchange for the two bridge Lambdas. Paths are independent of Terraform's state."""

import gzip
import hashlib
import io
import json
import os
import re
from functools import cache

import boto3
from botocore.exceptions import ClientError

ROOT = "arquifusion/bridge/v1/"
PENDING = ROOT + "pending/"
TABLES = {"telemetry", "incidents", "parts_movements"}
ARCHIVES = TABLES | {"incident_history", "stock_review"}
MAX_FILE = 32 * 1024 * 1024
CHUNK = 250


class DataError(ValueError):
    """Messages must be safe to report without logging raw records."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entry(identity, source, row):
    return {"id": digest(canonical(identity)), "source": source, "row": row}


def compressed(data):
    result = io.BytesIO()
    with gzip.GzipFile(fileobj=result, mode="wb", mtime=0, filename="") as stream:
        stream.write(data)
    return result.getvalue()


class Archive:
    def __init__(self, client, bucket, owner):
        self.client = client
        self.bucket = bucket
        self.owner = owner

    def args(self, key):
        return {"Bucket": self.bucket, "Key": key, "ExpectedBucketOwner": self.owner}

    def read(self, key, *, etag=None):
        params = self.args(key)
        if etag:
            params["IfMatch"] = etag
        try:
            result = self.client.get_object(**params)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        with result["Body"] as body:
            if result["ContentLength"] > MAX_FILE:
                raise DataError("object exceeds bridge size limit")
            return body.read()

    def json(self, key):
        data = self.read(key)
        return None if data is None else json.loads(data)

    def put_new(self, key, data, content_type="application/json"):
        try:
            self.client.put_object(**self.args(key), Body=data, ContentType=content_type, IfNoneMatch="*")
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
                return False
            raise
        return True

    def publish(self, kind, rows):
        if kind not in ARCHIVES or not rows:
            raise DataError("invalid archive group")
        # Sorting and hashing logical bytes makes identical replays produce the same key.
        data = b"".join(canonical(r) + b"\n" for r in sorted(rows, key=lambda r: (r["id"], r["source"])))
        if len(data) > MAX_FILE:
            raise DataError("batch exceeds bridge size limit")
        batch = digest(data)
        self.put_new(f"{ROOT}data/{kind}/{batch}.ndjson.gz", compressed(data), "application/gzip")
        if kind in TABLES:
            self.put_new(f"{PENDING}{kind}/{batch}.json", canonical({"batch": batch, "kind": kind, "rows": len(rows)}))
        return batch

    def batch(self, kind, batch, count=None):
        if (
            kind not in TABLES
            or not isinstance(batch, str)
            or not re.fullmatch(r"[a-f0-9]{64}", batch)
            or (count is not None and (type(count) is not int or count <= 0))
        ):
            raise DataError("invalid pending manifest")
        body = self.read(f"{ROOT}data/{kind}/{batch}.ndjson.gz")
        if body is None:
            raise DataError("missing immutable archive")
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE or digest(raw) != batch:
            raise DataError("archive checksum mismatch")
        rows = [json.loads(line) for line in raw.splitlines()]
        if not rows or (count is not None and len(rows) != count):
            raise DataError("manifest row count mismatch")
        return rows

    def requeue(self, kind, batch):
        rows = self.batch(kind, batch)
        self.put_new(f"{PENDING}{kind}/{batch}.json", canonical({"batch": batch, "kind": kind, "rows": len(rows)}))

    def page(self, prefix, after, limit):
        args = {"Bucket": self.bucket, "ExpectedBucketOwner": self.owner, "Prefix": prefix, "MaxKeys": limit}
        if after:
            args["StartAfter"] = after
        page = self.client.list_objects_v2(**args)
        return page.get("Contents", []), page["IsTruncated"]

    def acknowledge(self, pending_key):
        # Only the disposable notification is removed. Data, ownership and completion receipts are retained.
        if not pending_key.startswith(PENDING):
            raise DataError("only a bridge pending marker may be acknowledged")
        self.client.delete_object(**self.args(pending_key))


def verified_archive(client, bucket, owner):
    if not re.fullmatch(r"[0-9]{12}", owner) or "tfstate" in bucket or "terraform" in bucket:
        raise DataError("configure the dedicated ArquiFusion data bucket and its owner")
    scope = {"Bucket": bucket, "ExpectedBucketOwner": owner}
    region = client.get_bucket_location(**scope)["LocationConstraint"]
    tags = {t["Key"]: t["Value"] for t in client.get_bucket_tagging(**scope)["TagSet"]}
    expected = {"project": "arquifusion", "env": "dev", "purpose": "telemetry-lake", "owner": "rol1"}
    if region not in (None, "us-east-1") or not expected.items() <= tags.items():
        raise DataError("unexpected lake bucket region or project tags")
    return Archive(client, bucket, owner)


@cache
def configured_archive():
    return verified_archive(
        boto3.client("s3", region_name="us-east-1"), os.environ["AF_LAKE_BUCKET"], os.environ["AF_LAKE_ACCOUNT_ID"]
    )
