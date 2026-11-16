"""Private Cloud Run job. SQS is acknowledged only after BigQuery and DynamoDB agree."""

import hashlib
import io
import json
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import unquote_plus
from xml.etree import ElementTree

import requests
from google.api_core.exceptions import Conflict, NotFound
from google.auth.transport.requests import Request
from google.cloud import bigquery
from google.oauth2 import id_token

PROJECT = "airi-erp"
DATASET = "af_bridge"
BUCKET = "af-dev-arquifusion-lake-975050277936-us-east-1-an"
AUDIENCE = "https://arquifusion.airi-erp/bridge"
RAW = f"{PROJECT}.{DATASET}.telemetry_raw"
QUERY = f"SELECT * FROM \x60{PROJECT}.{DATASET}.daily_kpis\x60 ORDER BY date, machine_id"
KEY = re.compile(r"records/\d{4}/\d{2}/\d{2}/\d{2}/[A-Za-z0-9_.-]+\.parquet")
CHUNK = 3 * 1024 * 1024
MAX_OBJECT = 128 * 1024 * 1024
METRICS = {
    "utilizationPct": "utilization_pct",
    "operatingHours": "operating_hours",
    "idleHours": "idle_hours",
    "downHours": "down_hours",
    "overuseHours": "overuse_hours",
    "tonsMoved": "tons_moved",
    "fuelLiters": "fuel_liters",
    "fuelLitersPerTon": "fuel_liters_per_ton",
    "fuelLitersPerTonModel": "fuel_liters_per_ton_model",
    "costPerHour": "cost_per_hour",
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def queue_message(response):
    if response.text.lstrip().startswith("{"):

        def folded(value):
            return {name.lower(): child for name, child in value.items()}

        result = folded(response.json())
        for path in ("receivemessageresponse", "receivemessageresult"):
            if path in result:
                result = folded(result[path] or {})
        if not result or set(result) <= {"responsemetadata", "requestid"}:
            return None
        require(
            "messages" in result or "message" in result,
            "Unrecognized SQS JSON fields " + ",".join(sorted(result)[:12]),
        )
        values = result.get("messages", result.get("message")) or []
        if isinstance(values, dict):
            values = [values]
        if not values:
            return None
        item = folded(values[0])
        return item["receipthandle"], json.loads(item["body"])
    xml = ElementTree.fromstring(response.content)
    require(xml.tag.rsplit("}", 1)[-1] == "ReceiveMessageResponse", "Unexpected queue response")
    tags = set()
    for parent in xml.iter():
        tags.add(parent.tag.rsplit("}", 1)[-1].lower())
        children = {child.tag.rsplit("}", 1)[-1].lower(): child.text for child in parent}
        if "receipthandle" in children and "body" in children:
            return children["receipthandle"], json.loads(children["body"])
    empty_tags = {"receivemessageresponse", "receivemessageresult", "messages", "responsemetadata", "requestid"}
    require(tags <= empty_tags, "Unrecognized SQS XML fields " + ",".join(sorted(tags - empty_tags)[:12]))
    return None


class Gateway:
    def __init__(self):
        self.base = os.environ["BRIDGE_URL"].rstrip("/")
        require(
            re.fullmatch(r"https://[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com/v1", self.base),
            "Unexpected API origin",
        )
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + id_token.fetch_id_token(Request(), AUDIENCE)

    def request(self, method, route, *, allow_error=False, **kwargs):
        for attempt in range(4):
            res = self.session.request(
                method, self.base + "/" + route, timeout=(5, 28), allow_redirects=False, **kwargs
            )
            if res.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                if not res.ok and not allow_error:
                    raise RuntimeError(f"Bridge {method} {route.split('/')[0]} returned HTTP {res.status_code}")
                require(res.status_code < 300 or res.status_code >= 400, "Unexpected redirect")
                return res
            time.sleep(2**attempt)

    def download(self, record):
        item = record["s3"]["object"]
        key, size, etag = unquote_plus(item["key"]), item["size"], item["eTag"].strip('"')
        require(
            record["awsRegion"] == "us-east-1"
            and record["eventSource"] == "aws:s3"
            and record["eventName"].startswith("ObjectCreated:")
            and record["s3"]["bucket"]["name"] == BUCKET,
            "Unexpected event source",
        )
        require(
            KEY.fullmatch(key) and 8 <= size <= MAX_OBJECT and re.fullmatch(r"[0-9a-fA-F-]{32,70}", etag),
            "Invalid Parquet object",
        )
        out = io.BytesIO()
        for start in range(0, size, CHUNK):
            end = min(start + CHUNK, size) - 1
            res = self.request(
                "GET",
                "files/" + key.removeprefix("records/"),
                headers={
                    "Accept": "application/octet-stream",
                    "Range": f"bytes={start}-{end}",
                    "If-Match": f'"{etag}"',
                },
            )
            require(
                len(res.content) == end - start + 1 and (res.status_code == 206 or start == 0 and end + 1 == size),
                "Invalid S3 byte range",
            )
            out.write(res.content)
        data = out.getvalue()
        require(data[:4] == data[-4:] == b"PAR1", "Invalid Parquet envelope")
        if "-" not in etag:
            require(hashlib.md5(data, usedforsecurity=False).hexdigest() == etag.lower(), "S3 ETag mismatch")
        return data

    def write_kpi(self, row):
        body = {
            "machineId": row["machine_id"],
            "date": row["date"].isoformat(),
            "model": row["model"],
            "messageId": row["last_message_id"],
            "sourceReadings": int(row["source_readings"]),
            "dayReadings": int(row["day_readings"]),
            "fuelAnomaly": bool(row["fuel_anomaly"]),
        }
        body.update({key: row[column] for key, column in METRICS.items() if row[column] is not None})
        key = {k: body[k] for k in ("machineId", "date")}

        def present():
            item = self.request("POST", "status", json=key).json().get("Item", {})
            if not item:
                return False
            require(
                item.get("operationId", {}).get("S") == "AF-BRIDGE-PERU"
                and item.get("sourceProject", {}).get("S") == PROJECT,
                "Foreign KPI",
            )
            count = int(item["sourceDayReadings"]["N"])
            return count > body["dayReadings"] or (
                count == body["dayReadings"] and item["sourceMessageId"]["S"] == body["messageId"]
            )

        if present():
            return "already_present"
        res = self.request("POST", "kpi", json=body, allow_error=True)
        if res.status_code not in (200, 400, 409) or not present():
            raise RuntimeError(f"KPI transaction not confirmed (HTTP {res.status_code})")
        return "verified"


def load_file(client, data):
    digest = hashlib.sha256(data).hexdigest()
    # A deterministic BigQuery job handles redelivery, concurrent jobs and ambiguous network replies.
    # Permanent input errors stay on the source queue and eventually reach its DLQ.
    job = None
    for attempt in range(3):
        job_id = f"af_load_{digest}_{attempt}"
        try:
            job = client.get_job(job_id, location="US")
        except NotFound:
            config = bigquery.LoadJobConfig(
                source_format="PARQUET",
                write_disposition="WRITE_APPEND",
                create_disposition="CREATE_NEVER",
                labels={"project": "arquifusion", "purpose": "bridge"},
            )
            try:
                job = client.load_table_from_file(
                    io.BytesIO(data), RAW, size=len(data), job_id=job_id, job_config=config, location="US"
                )
            except Conflict:
                job = client.get_job(job_id, location="US")
        require(
            job.job_type == "load"
            and job.job_id == job_id
            and str(job.destination) == RAW
            and job.location == "US"
            and job.project == PROJECT,
            "Unexpected load job",
        )
        try:
            job.result(timeout=180)
        except Exception:
            if (job.error_result or {}).get("reason") not in {
                "backendError",
                "internalError",
                "rateLimitExceeded",
                "jobBackendError",
                "jobInternalError",
                "jobRateLimitExceeded",
            }:
                raise
            continue
        require((job.output_rows or 0) > 0, "Empty batch")
        return digest, job.job_id, job.output_rows
    raise RuntimeError("Load attempts exhausted")


def run():
    gateway = Gateway()
    client = bigquery.Client(project=PROJECT, location="US")
    processed = 0
    until = time.monotonic() + 400
    while processed < 10 and time.monotonic() < until:
        message = queue_message(gateway.request("POST", "lease", json={}))
        if message is None:
            break
        receipt, event = message
        if event.get("Event") == "s3:TestEvent":
            require(event.get("Bucket") == BUCKET, "Unexpected test event")
        else:
            records = event.get("Records", [])
            require(records, "Missing S3 object notification")
            for record in records:
                digest, job, rows = load_file(client, gateway.download(record))
                config = bigquery.QueryJobConfig(
                    maximum_bytes_billed=64 * 1024 * 1024,
                    labels={"project": "arquifusion", "purpose": "return-kpi"},
                    use_query_cache=False,
                )
                query = client.query(QUERY, location="US", job_config=config)
                summary = {}
                for row in query.result(timeout=160):
                    state = gateway.write_kpi(row)
                    summary[state] = summary.get(state, 0) + 1
                require(summary, "No KPI found for the enrolled fleet in the 30-day window")
                print(
                    json.dumps(
                        {
                            "utc": datetime.now(UTC).isoformat(),
                            "sha256": digest,
                            "loadJob": job,
                            "queryJob": query.job_id,
                            "sourceRows": rows,
                            "kpi": summary,
                        }
                    ),
                    flush=True,
                )
        gateway.request("POST", "complete", json={"receipt": receipt})
        processed += 1
    print(json.dumps({"deliveriesAcknowledged": processed}), flush=True)


if __name__ == "__main__":
    run()
