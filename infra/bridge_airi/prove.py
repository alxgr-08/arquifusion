"""Verify both providers, scheduled redelivery, the source telemetry and the final maintenance-independent KPI."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import requests
from google.cloud import bigquery

from infra.bridge_airi import observe, smoke
from services.lake_to_bigquery.telemetry import bigquery_row, telemetry_row

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build/bridge-permanent"


def json_value(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    return value.isoformat()


def proof():
    fixture = json.loads((smoke.OUT / "inputs.json").read_text(encoding="utf-8"))
    inputs = fixture["rows"]
    source = [json.loads((smoke.OUT / f"found-{i}.json").read_text(encoding="utf-8")) for i in (1, 2)]
    replay = json.loads((smoke.OUT / "replay.json").read_text(encoding="utf-8"))
    before = json.loads((smoke.OUT / "before-replay.json").read_text(encoding="utf-8"))
    require = smoke.require
    day = inputs[1]["timestamp"][:10]
    ids = [r["messageId"] for r in inputs]
    require(
        source[0]["key"] != source[1]["key"] and replay["sha256"] == source[0]["sha256"],
        "Missing independent S3 objects",
    )
    aws = smoke.cloud()
    ddb = aws.resource("dynamodb").Table("af-dev-arquifusion")
    key = {"PK": "MACHINE#" + smoke.MACHINE, "SK": "KPI#" + day}
    actual = ddb.get_item(Key=key, ConsistentRead=True)["Item"]
    require(json.loads(json.dumps(actual, default=str)) == before["kpi"], "KPI changed on redelivery")
    require(
        actual["sourceReadings"] == 2
        and actual["tonsMoved"] == 38
        and actual["fuelLiters"] == 17
        and actual["operatingHours"] == Decimal("0.5")
        and actual["sourceMessageId"] == ids[1],
        "Unexpected daily KPI",
    )
    n = ddb.query(
        KeyConditionExpression="PK=:p", ExpressionAttributeValues={":p": key["PK"]}, ConsistentRead=True, Select="COUNT"
    )["Count"]
    require(n == 2, "Expected exactly one profile and one KPI")
    client = bigquery.Client(project="airi-erp", location="US")
    parameters = [bigquery.ArrayQueryParameter("ids", "STRING", ids)]

    def select(sql):
        config = bigquery.QueryJobConfig(
            query_parameters=parameters, maximum_bytes_billed=64 * 1024 * 1024, use_query_cache=False
        )
        job = client.query(sql, job_config=config, location="US")
        return list(job.result(timeout=60)), job.job_id

    checks = {}
    for name in ("telemetry_raw", "telemetry"):
        rows, job = select(f"SELECT * FROM \x60airi-erp.af_bridge.{name}\x60 WHERE messageId IN UNNEST(@ids)")
        verified = {r["messageId"]: bigquery_row(dict(r)) for r in rows}
        require(
            len(rows) == 2 and all(verified[p["messageId"]] == telemetry_row(p) for p in inputs),
            "Telemetry mismatch in " + name,
        )
        checks[name] = {"rows": len(rows), "allFieldsMatch": True, "verificationJob": job}
    rows = client.query(
        "SELECT * FROM \x60airi-erp.af_bridge.daily_kpis\x60 WHERE machine_id=@m AND date=DATE(@d)",
        job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=64 * 1024 * 1024,
            query_parameters=[
                bigquery.ScalarQueryParameter("m", "STRING", smoke.MACHINE),
                bigquery.ScalarQueryParameter("d", "STRING", day),
            ],
        ),
    ).result()
    (kpi,) = list(rows)
    require(
        kpi["source_readings"] == 2
        and kpi["tons_moved"] == actual["tonsMoved"]
        and kpi["fuel_liters"] == actual["fuelLiters"],
        "Clouds disagree",
    )
    http = observe.session()
    schedule = observe.fetch(http, "https://cloudscheduler.googleapis.com/v1/" + observe.SCHEDULE)
    runs = observe.executions(http)
    execution_logs = observe.logs(http)
    first = [log for log in execution_logs if log.get("sha256") == source[0]["sha256"]]
    require(
        len({log["execution"] for log in first}) >= 2
        and len({log["loadJob"] for log in first}) == 1
        and any(log["kpi"].get("already_present", 0) > 0 for log in first),
        "Awaiting verified replay from another Cloud Run execution",
    )
    scheduled = [
        r
        for r in runs
        if r["succeeded"] == 1 and datetime.fromisoformat(r["created"].replace("Z", "+00:00")).minute == 52
    ]
    require(
        schedule["state"] == "ENABLED"
        and scheduled
        and any(r["name"] in {e["execution"] for e in first if e["kpi"].get("already_present")} for r in scheduled),
        "Awaiting the hourly Cloud Scheduler execution",
    )
    load_jobs = []
    for obj in source:
        job = client.get_job("af_load_" + obj["sha256"] + "_0", location="US")
        require(job.state == "DONE" and job.output_rows == 1 and not job.error_result, "Missing load job")
        require(job.user_email == "af-bridge-worker@airi-erp.iam.gserviceaccount.com", "Wrong load identity")
        load_jobs.append(job.job_id)
    queues = {}
    for queue in ("af-dev-airi-delivery", "af-dev-airi-dead"):
        q = aws.client("sqs")
        url = q.get_queue_url(QueueName=queue)["QueueUrl"]
        counts = q.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]
        )["Attributes"]
        queues[queue] = {k: int(v) for k, v in counts.items()}
    pages = aws.client("apigateway").get_paginator("get_rest_apis").paginate()
    (bridge_api,) = [
        v
        for page in pages
        for v in page["items"]
        if v.get("name") == "af-dev-airi-bridge" and v.get("tags", {}).get("project") == "arquifusion"
    ]
    api = f"https://{bridge_api['id']}.execute-api.us-east-1.amazonaws.com/v1"
    auth = {route: requests.post(api + "/" + route, json={}, timeout=8).status_code for route in ("lease", "kpi")}
    require(all(x == 401 for x in auth.values()), "Anonymous route accepted")
    state = aws.client("s3").head_object(
        Bucket="af-dev-tfstate-975050277936", Key="aws/terraform.tfstate", ExpectedBucketOwner=smoke.ACCOUNT
    )
    plan = BUILD / "final.tfplan"
    require(
        plan.is_file() and "No changes" in (BUILD / "final-plan.log").read_text(encoding="utf-8"),
        "Missing final reviewed plan",
    )
    return {
        "verifiedAtUtc": datetime.now(UTC).isoformat(),
        "scope": "telemetry and daily KPI",
        "awsRegion": smoke.REGION,
        "operation": smoke.OPERATION,
        "machine": smoke.MACHINE,
        "kpiDayUtc": day,
        "sourceObjects": source,
        "replayedNotification": replay,
        "google": {
            "project": "airi-erp",
            "dataset": "af_bridge",
            "datasetLocation": "US",
            "checks": checks,
            "loadJobs": load_jobs,
            "loadIdentity": "af-bridge-worker@airi-erp.iam.gserviceaccount.com",
            "scheduler": {
                "name": "af-bridge-hourly",
                "state": schedule["state"],
                "cron": schedule["schedule"],
                "timezone": schedule["timeZone"],
                "regularExecutions": scheduled,
            },
            "processedObjects": [x for x in execution_logs if x.get("sha256") in {o["sha256"] for o in source}],
        },
        "dynamo": {"table": ddb.name, "key": key, "partitionItems": n, "kpiUnchangedOnReplay": True, "kpi": actual},
        "anonymousHTTP": auth,
        "queues": queues,
        "incidentRule": "DISABLED",
        "mainTerraformStateLastModified": state["LastModified"],
        "image": json.loads((BUILD / "image.json").read_text()),
        "planSHA256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(BUILD.glob("*.tfplan"))},
        "finalTerraform": {"create": 0, "update": 0, "delete": 0},
    }


if __name__ == "__main__":
    evidence = proof()
    path = ROOT / "docs/evidencias/2026-11-15-puente-permanente.json"
    path.write_text(json.dumps(evidence, indent=2, default=json_value) + "\n", encoding="utf-8")
    print("Verified:", path)
