"""Offline cross-service rehearsal. DynamoDB and S3 use boto3 resources against moto.

The orchestrator executes the checked-in ASL, callback events contain synthetic verified
claims, and a BigQuery double accepts the actual files. No Google token, BQML training
job or cloud execution is involved.
"""

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from af_shared.ddb import to_ddb
from af_shared.fakes import Recorder
from moto import mock_aws

from services.bigquery_sync_back.handler import return_selected_kpi
from services.lake_to_bigquery import handler as intake
from services.lake_to_bigquery.tests.bigquery_double import STAGING, BigQueryDouble
from services.stream_exporter.handler import export
from services.stream_exporter.lake_io import Archive
from services.stream_exporter.tests.samples import BUCKET, OWNER

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))
import ensayo_kit  # noqa: E402
from flow_harness import Clock  # noqa: E402
from test_ensayo_lab import Operator, lab_harness  # noqa: E402


def rehearsal_table(region):
    def keys(prefix):
        return [
            {"AttributeName": prefix + "PK", "KeyType": "HASH"},
            {"AttributeName": prefix + "SK", "KeyType": "RANGE"},
        ]

    return boto3.resource("dynamodb", region_name=region).create_table(
        TableName="af-dev-arquifusion",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
        ],
        KeySchema=keys(""),
        GlobalSecondaryIndexes=[
            {"IndexName": index, "KeySchema": keys(index), "Projection": {"ProjectionType": "ALL"}}
            for index in ("GSI1", "GSI2")
        ],
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
    )


def stream_records(arn, region):
    streams = boto3.client("dynamodbstreams", region_name=region)
    records = []
    for shard in streams.describe_stream(StreamArn=arn)["StreamDescription"]["Shards"]:
        cursor = streams.get_shard_iterator(StreamArn=arn, ShardId=shard["ShardId"], ShardIteratorType="TRIM_HORIZON")[
            "ShardIterator"
        ]
        while cursor:
            page = streams.get_records(ShardIterator=cursor)
            records.extend({**row, "eventSourceARN": arn} for row in page["Records"])
            if not page["Records"]:
                break
            cursor = page.get("NextShardIterator")
    return records


def test_health_workflow_callbacks_and_lake_share_one_synthetic_case(load_handler, monkeypatch):
    region = "us-east-1"
    monkeypatch.delenv("AF_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", region)
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    kit = ensayo_kit.build(7, start)
    with mock_aws():
        h = lab_harness(kit, case2=True)
        table = rehearsal_table(region)
        for item in h.table.items.values():
            table.put_item(Item=to_ddb(item))
        h.table = table
        h.clock = Clock("2026-11-16T15:10:00Z")
        health = load_handler("health_evaluator")
        events = Recorder({"put_events": {"FailedEntryCount": 0, "Entries": [{"EventId": "test-event"}]}})
        monkeypatch.setattr(health, "_cache", {"table": table, "events": events})
        table.put_item(
            Item={
                "PK": "MODEL#EXC-320",
                "SK": "BASELINE",
                "metrics": {"temperatureC": {"mean": 78, "std": 2}, "vibrationMmS": {"mean": 4, "std": 1}},
            }
        )
        payload = json.loads((ROOT / "contracts/examples/telemetry.ok.json").read_text(encoding="utf-8"))
        payload.update(machineId=kit["run.json"]["machine"], operationId=kit["run.json"]["operation"], model="EXC-320")
        payload["location"]["zoneId"] = kit["anomaly-caso2-rodamiento.json"]["detail"]["zoneId"]
        original = []
        for step in range(8):
            payload["timestamp"] = (start + timedelta(minutes=step)).isoformat().replace("+00:00", "Z")
            payload["messageId"] = str(uuid5(NAMESPACE_URL, "arquifusion/rehearsal/" + str(step)))
            payload["metrics"].update(temperatureC=78 if step == 0 else 99, vibrationMmS=4 if step == 0 else 10)
            result = health.process(payload)
            assert result["result"] == "OK"
            original.append(json.loads(json.dumps(payload)))
            if events.of("put_events"):
                break
        [entry] = events.of("put_events")[0]["Entries"]
        detail = json.loads(entry["Detail"])
        assert detail["machineId"] == payload["machineId"] and detail["severity"] in {"WARNING", "CRITICAL"}
        assert health.process(payload)["result"] == "SKIPPED"
        envelope = {
            **kit["anomaly-caso2-rodamiento.json"],
            "source": entry["Source"],
            "detail-type": entry["DetailType"],
            "detail": detail,
            "time": detail["detectedAt"],
        }
        api = load_handler("api")
        operator = Operator(api, h, kit)
        run, _ = h.run_incident(envelope, operator)
        assert run.visited[-1] == "Done" and len(operator.answers) == 2
        retry, _ = h.run_incident(envelope, operator)
        assert retry.visited[-1] == "DuplicateIgnored"
        rows = table.query(
            KeyConditionExpression="PK = :machine",
            ExpressionAttributeValues={":machine": "MACHINE#" + payload["machineId"]},
        )["Items"]
        [incident] = [r for r in rows if r["SK"].startswith("INCIDENT#")]
        assert incident["state"] == "RESOLVED"
        assert len([r for r in rows if r["SK"].startswith("MAINT#")]) == 1
        assert len([r for r in rows if r["SK"].startswith("ACK#")]) == 2

        bq = BigQueryDouble()
        bq.project = "arquifusion"
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=BUCKET)
        archive = Archive(s3, BUCKET, OWNER)
        parquet = io.BytesIO()
        physical_rows = [{**row, "timestamp": datetime.fromisoformat(row["timestamp"])} for row in original]
        pq.write_table(pa.Table.from_pylist(physical_rows), parquet, compression="snappy")
        s3.put_object(Bucket=BUCKET, Key="incoming/telemetry/rehearsal.parquet", Body=parquet.getvalue())
        arn = table.latest_stream_arn
        records = stream_records(arn, region)
        assert len(records) > 0
        monkeypatch.setattr(intake, "configured_archive", lambda: archive)
        monkeypatch.setattr(intake, "bigquery_client", lambda: bq)
        monkeypatch.setenv("AF_PARQUET_PREFIX", "incoming/telemetry/")
        monkeypatch.setenv("AF_BQ_STAGING_DATASET", STAGING)
        for _ in range(2):
            assert export(records, archive, arn)["batchItemFailures"] == []
            assert (
                intake.handler({}, SimpleNamespace(get_remaining_time_in_millis=lambda: 60_000))["pending"]["failures"]
                == []
            )
        dest = bq.project + ".af_lake."
        saved = bq.tables[dest + "telemetry"]
        assert sorted(r["messageId"] for r in saved) == sorted(r["messageId"] for r in original)
        assert [r["incidentId"] for r in bq.tables[dest + "incidents"]] == [incident["incidentId"]]
        [used] = bq.tables[dest + "parts_movements"]
        assert used["warehouseId"] == kit["run.json"]["warehouse"] and used["qty"] == 1

        # af_out is a result fixture derived from the same loaded facts. The SQL/ML engines run in GCP.
        class OutputDataset:
            project = "arquifusion"

            def query(self, statement, *, job_config, location):
                params = {p.name: p.value for p in job_config.query_parameters}
                assert location == "US" and "arquifusion.af_out.daily_kpis" in statement
                assert set(params) == {"machine_id", "operation_id", "kpi_date"}
                assert {r["machineId"] for r in saved} == {params["machine_id"]}
                assert {r["operationId"] for r in saved} == {params["operation_id"]}
                assert {r["timestamp"][:10] for r in saved} == {str(params["kpi_date"])}
                output = {
                    **params,
                    "date": start.date(),
                    "model": payload["model"],
                    "idle_hours": 0,
                    "operating_hours": 1,
                    "overuse_hours": 0,
                    "utilization_pct": 100,
                    "cost_per_hour": 75,
                    "tons_moved": sum(r["metrics"].get("loadTons") or 0 for r in saved),
                }
                return SimpleNamespace(result=lambda *, timeout: [output])

        selection = {
            "machine_id": payload["machineId"],
            "operation_id": payload["operationId"],
            "kpi_date": start.date(),
        }
        assert return_selected_kpi(OutputDataset(), table, **selection)["status"] == "written"
        assert return_selected_kpi(OutputDataset(), table, **selection)["status"] == "already_present"
        request = dict(kit["api-get-incident.json"])
        request["rawPath"] = f"/operations/{payload['operationId']}/kpis"
        request["queryStringParameters"] = {"date": start.date().isoformat()}
        response = api.route(request, table, None, h.clock())
        assert response["statusCode"] == 200
        [kpi] = json.loads(response["body"])
        assert kpi["machineId"] == payload["machineId"] and kpi["utilizationPct"] == 100
