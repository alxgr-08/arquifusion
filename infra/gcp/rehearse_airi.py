"""Relay one identified Academy Parquet object into the isolated airi-erp demonstration dataset."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import boto3
import google.auth
from google.api_core.exceptions import Conflict, NotFound
from google.cloud import bigquery
from google.oauth2.credentials import Credentials as UserCredentials

ROOT = Path(__file__).resolve().parents[2]
if __name__ == "__main__":
    sys.path[:0] = [str(ROOT), str(ROOT / "shared")]

from af_shared import ddb, keys  # noqa: E402

from infra.gcp.rehearse_object import inspect_file  # noqa: E402
from services.bigquery_sync_back.handler import kpi_items  # noqa: E402
from services.bigquery_sync_back.handler import to_ddb as kpi_to_ddb  # noqa: E402
from services.lake_to_bigquery.load_jobs import load_attempt  # noqa: E402
from services.lake_to_bigquery.telemetry import bigquery_row  # noqa: E402
from services.stream_exporter.lake_io import MAX_FILE  # noqa: E402

PROJECT = "airi-erp"
DATASET = "af_bridge_demo"
ACCOUNT = "975050277936"
REGION = "us-east-1"
TABLE = "af-dev-arquifusion"
DATASET_LABELS = {"project": "arquifusion", "purpose": "bridge-demo", "owner": "rol1"}
TTL_MS = 7 * 86_400_000
MAX_QUERY_BYTES = 10 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def dataset_client():
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"], quota_project_id=PROJECT)
    require(isinstance(credentials, UserCredentials), "Select the authorized operator's Google session")
    client = bigquery.Client(project=PROJECT, location="US", credentials=credentials)
    ds = client.get_dataset(f"{PROJECT}.{DATASET}")
    require(
        ds.location == "US"
        and DATASET_LABELS.items() <= ds.labels.items()
        and ds.default_table_expiration_ms == TTL_MS,
        "Unexpected dataset ownership, location or expiration",
    )
    require(
        not any(
            e.entity_id in ("allUsers", "allAuthenticatedUsers")
            for e in ds.access_entries
            if isinstance(e.entity_id, str)
        ),
        "Public dataset grant detected",
    )
    return client


def get_source(folder, session):
    run = folder.name
    require(re.fullmatch(r"[0-9]{8}t[0-9]{6}z-[a-f0-9]{10}", run), "Unexpected test run")
    evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    bucket = "af-dev-arquifusion-pq-" + run
    require(
        evidence["run"] == run and evidence["bucket"] == bucket and evidence["region"] == REGION,
        "Unexpected temporary bucket",
    )
    source = json.loads((folder / "source-reading.json").read_text(encoding="utf-8"))
    require(
        re.fullmatch(r"TL[A-Z]{3}-[0-9]{4}", source["machineId"])
        and re.fullmatch(r"AF-LAKE-TEST-[A-F0-9]{32}", source["operationId"])
        and source["model"] == source["operationId"].replace("AF-LAKE-TEST-", "AF-LAKE-MODEL-"),
        "Only isolated simulator test machines may participate",
    )
    downloaded = next(item for item in evidence["checks"] if item["event"] == "downloaded")
    key = downloaded["key"]
    require(
        re.fullmatch(r"records/[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9]{2}/[\w.-]+\.parquet", key),
        "Unexpected Firehose source key",
    )
    s3 = session.client("s3")
    scope = {"Bucket": bucket, "ExpectedBucketOwner": ACCOUNT}
    tags = {i["Key"]: i["Value"] for i in s3.get_bucket_tagging(**scope)["TagSet"]}
    require(
        tags == {"project": "arquifusion", "env": "dev", "purpose": "parquet-smoke", "run": run}, "S3 ownership differs"
    )
    require(s3.get_bucket_location(**scope)["LocationConstraint"] is None, "Unexpected S3 region")
    head = s3.head_object(**scope, Key=key)
    require(0 < head["ContentLength"] <= MAX_FILE, "Unexpected object size")
    response = s3.get_object(**scope, Key=key, IfMatch=head["ETag"])
    with response["Body"] as body:
        content = body.read()
    digest = hashlib.sha256(content).hexdigest()
    require(digest == downloaded["sha256"], "S3 object differs from the verified Firehose delivery")
    row = inspect_file(content, source)
    return content, row, digest, f"s3://{bucket}/{key}"


def kpi_sql(raw_table):
    # Reuse the analytics query's SELECT and KPI definitions. Its unscoped multi-day replacement DDL is for the history.
    source = (ROOT / "analytics/sql/10_daily_kpis.sql").read_text(encoding="utf-8")
    start = source.index("WITH readings AS (")
    body = source[start:].strip().removesuffix(";")
    expected_source = "FROM af_lake.telemetry"
    expected_filter = "WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL lookback_days DAY)"
    require(body.count(expected_source) == body.count(expected_filter) == 1, "Review the changed analytics query")
    body = body.replace(expected_source, f"FROM `{raw_table}`").replace(
        expected_filter,
        "WHERE messageId = @message_id AND machineId = @machine_id "
        "AND operationId = @operation_id AND DATE(timestamp) = @kpi_date",
    )
    for variable in ("fuel_price_per_liter", "fixed_cost_per_day", "fuel_ratio_alert"):
        body = re.sub(rf"\b{variable}\b", "@" + variable, body)
    return (
        "SELECT daily.*, @message_id AS source_message_id, @operation_id AS operation_id "
        "FROM (\n" + body + "\n) AS daily"
    )


def compute_kpi(client, target, raw_table, row, job_id):
    sql = kpi_sql(raw_table)
    params = [
        bigquery.ScalarQueryParameter(name, typ, value)
        for name, typ, value in (
            ("message_id", "STRING", row["messageId"]),
            ("machine_id", "STRING", row["machineId"]),
            ("operation_id", "STRING", row["operationId"]),
            ("kpi_date", "DATE", date.fromisoformat(row["timestamp"][:10])),
            ("fuel_price_per_liter", "FLOAT64", 1.10),
            ("fixed_cost_per_day", "FLOAT64", 2400),
            ("fuel_ratio_alert", "FLOAT64", 1.20),
        )
    ]
    config = bigquery.QueryJobConfig(
        destination=target,
        write_disposition="WRITE_EMPTY",
        use_legacy_sql=False,
        query_parameters=params,
        maximum_bytes_billed=MAX_QUERY_BYTES,
        labels={"project": "arquifusion", "purpose": "bridge-demo"},
    )
    try:
        job = client.get_job(job_id, location="US")
    except NotFound:
        try:
            job = client.query(sql, job_id=job_id, job_config=config, location="US", job_retry=None)
        except Conflict:
            job = client.get_job(job_id, location="US")
    require(
        job.job_type == "query"
        and job.project == PROJECT
        and job.location == "US"
        and job.query == sql
        and job.job_id == job_id
        and str(job.destination) == target
        and job.write_disposition == "WRITE_EMPTY"
        and [p.to_api_repr() for p in job.query_parameters] == [p.to_api_repr() for p in params],
        "Unexpected existing KPI job",
    )
    job.result(timeout=120)
    return job


def single_row(client, name):
    table = client.get_table(name)
    require(table.num_rows == 1 and table.expires is not None, "Expected one expiring BigQuery row")
    rows = list(client.list_rows(table, max_results=2))
    require(len(rows) == 1, "Unexpected row count")
    return dict(rows[0].items()), table.expires.isoformat()


def store_result(table, source, result, digest, job_id):
    machine, op = source["machineId"], source["operationId"]
    profile = ddb.to_ddb(
        keys.machine_profile(machine)
        | keys.machine_gsi1(op, source["status"], machine)
        | {
            "machineId": machine,
            "operationId": op,
            "model": source["model"],
            "machineType": source["machineType"],
            "lat": source["location"]["lat"],
            "lon": source["location"]["lon"],
            "zoneId": source["location"]["zoneId"],
            "status": source["status"],
            "lastSeen": source["timestamp"],
            "bridgeMessageId": source["messageId"],
        }
    )
    kpi = kpi_to_ddb(
        kpi_items([result], op)[0]
        | {
            "operationId": op,
            "sourceMessageId": source["messageId"],
            "sourceSha256": digest,
            "sourceProject": PROJECT,
            "sourceJobId": job_id,
        }
    )
    expected = [profile, kpi]
    targets = [{k: item[k] for k in ("PK", "SK")} for item in expected]

    def snapshot():
        requests = [{"Get": {"TableName": table.name, "Key": key}} for key in targets]
        return [r.get("Item") for r in table.meta.client.transact_get_items(TransactItems=requests)["Responses"]]

    present = snapshot()
    if present == expected:
        return "already_present", targets[1]
    require(present == [None, None], "Refusing to overwrite an existing machine or KPI")
    count = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": profile["PK"]},
        ConsistentRead=True,
        Select="COUNT",
        Limit=1,
    )["Count"]
    require(count == 0, "Synthetic machine partition already belongs to another case")
    try:
        ddb.transact_write(
            table, [{"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(PK)"}} for item in expected]
        )
    except table.meta.client.exceptions.TransactionCanceledException as exc:
        if not ddb.is_transaction_conditional_failure(exc) or snapshot() != expected:
            raise
        return "already_present", targets[1]
    require(snapshot() == expected, "Cannot verify DynamoDB profile and KPI")
    return "written", targets[1]


def relay(folder):
    session = boto3.Session(profile_name="default", region_name=REGION)
    require(session.client("sts").get_caller_identity()["Account"] == ACCOUNT, "Wrong AWS Academy account")
    rule_state = session.client("events").describe_rule(Name="af-dev-incident", EventBusName="af-dev-bus")["State"]
    require(rule_state == "DISABLED", "The incident rule is enabled")
    table = session.resource("dynamodb").Table(TABLE)
    require(table.table_arn == f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE}", "Unexpected DynamoDB table")
    tags = {
        t["Key"]: t["Value"]
        for t in session.client("dynamodb").list_tags_of_resource(ResourceArn=table.table_arn)["Tags"]
    }
    require(
        {"project": "arquifusion", "env": "dev", "owner": "rol1"}.items() <= tags.items(),
        "Unexpected DynamoDB ownership",
    )
    mappings = session.client("lambda").get_paginator("list_event_source_mappings")
    for page in mappings.paginate(EventSourceArn=table.latest_stream_arn):
        require(
            all(m["State"] == "Disabled" for m in page["EventSourceMappings"]), "An automatic stream consumer is active"
        )
    data, row, digest, uri = get_source(folder, session)
    client = dataset_client()
    suffix = UUID(row["messageId"]).hex
    prefix = f"{PROJECT}.{DATASET}"
    raw, target = f"{prefix}.telemetry_{suffix}", f"{prefix}.daily_kpi_{suffix}"
    opened = datetime.fromisoformat(row["timestamp"])
    config = bigquery.LoadJobConfig(
        source_format="PARQUET",
        write_disposition="WRITE_EMPTY",
        create_disposition="CREATE_IF_NEEDED",
        max_bad_records=0,
        labels={"project": "arquifusion", "purpose": "bridge-demo"},
    )
    load_id = f"af_bridge_load_{suffix}_{digest[:16]}"
    load = load_attempt(
        client, raw, data, config, load_id, opened_at=opened, now=datetime.now(UTC), timeout=120, expected_rows=1
    )
    require(load is not None, "BigQuery load failed with a retryable error")
    fetched, raw_expiry = single_row(client, raw)
    require(bigquery_row(fetched) == row, "BigQuery telemetry differs from the complete simulator reading")
    query_id = f"af_bridge_kpi_{suffix}_{hashlib.sha256(kpi_sql(raw).encode()).hexdigest()[:16]}"
    query = compute_kpi(client, target, raw, row, query_id)
    result, kpi_expiry = single_row(client, target)
    actual = (
        result["machine_id"],
        result["operation_id"],
        result["source_message_id"],
        result["model"],
        str(result["date"]),
    )
    expected = (row["machineId"], row["operationId"], row["messageId"], row["model"], row["timestamp"][:10])
    require(actual == expected, "The analytical result belongs to a different reading")
    status, key = store_result(table, row, result, digest, query.job_id)
    return {
        "status": status,
        "messageId": row["messageId"],
        "machineId": row["machineId"],
        "operationId": row["operationId"],
        "utcDay": str(result["date"]),
        "source": uri,
        "bytes": len(data),
        "sha256": digest,
        "region": REGION,
        "eventBridge": rule_state,
        "bigqueryLocation": "US",
        "telemetryTable": raw,
        "telemetryRows": 1,
        "telemetryExpiresAt": raw_expiry,
        "loadJob": load.job_id,
        "kpiTable": target,
        "kpiRows": 1,
        "kpiExpiresAt": kpi_expiry,
        "queryJob": query.job_id,
        "queryBytesBilled": query.total_bytes_billed,
        "dynamoKey": key,
        "dynamoTable": TABLE,
        "operatingHours": result["operating_hours"],
        "tonsMoved": result["tons_moved"],
        "fuelLiters": result["fuel_liters"],
        "utilizationPct": result["utilization_pct"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(relay(args.run_folder.resolve()), ensure_ascii=False, indent=2))
