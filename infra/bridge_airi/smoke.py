"""Send the two authorized synthetic readings and replay one verified S3 notification."""

import argparse
import hashlib
import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

import boto3
import pyarrow.parquet as pq
from botocore.config import Config
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build/bridge-permanent/smoke"
BUCKET = "af-dev-arquifusion-lake-975050277936-us-east-1-an"
ACCOUNT = "975050277936"
REGION = "us-east-1"
MACHINE = "BRG-8115"
OPERATION = "AF-BRIDGE-PERU"
QUEUE = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/af-dev-airi-delivery"


def require(ok, text):
    if not ok:
        raise ValueError(text)


def save(path, data):
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def prepare():
    folder = OUT
    folder.mkdir(parents=True, exist_ok=True)
    evidence = folder / "inputs.json"
    if evidence.exists():
        return json.loads(evidence.read_text(encoding="utf-8"))
    example = json.loads((ROOT / "contracts/examples/telemetry.ok.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "contracts/telemetry.schema.json").read_text(encoding="utf-8"))
    now = datetime.now(UTC).replace(microsecond=0)
    require(now.hour or now.minute >= 16, "Prepare both readings on the same UTC day")
    rows = []
    for i in range(2):
        row = deepcopy(example)
        row.update(
            machineId=MACHINE,
            operationId=OPERATION,
            model="AF-BRIDGE-DEMO",
            messageId=str(uuid4()),
            timestamp=(now - timedelta(minutes=15 * (1 - i))).isoformat().replace("+00:00", "Z"),
            location={"lat": -10.684, "lon": -76.263, "zoneId": "PASCO"},
        )
        row["metrics"].update(loadTons=18.0 + 2.0 * i, fuelRateLph=32.0 + 4.0 * i)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(row)
        rows.append(row)
    bundle = {"run": uuid4().hex, "rows": rows}
    save(evidence, bundle)
    return bundle


def cloud():
    session = boto3.Session(profile_name="default", region_name=REGION)
    require(session.client("sts").get_caller_identity()["Account"] == ACCOUNT, "Unexpected Academy account")
    state = session.client("events").describe_rule(Name="af-dev-incident", EventBusName="af-dev-bus")["State"]
    require(state == "DISABLED", "Unexpected incident rule state")
    s3 = session.client("s3")
    tags = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)["TagSet"]}
    require(tags.get("project") == "arquifusion" and tags.get("purpose") == "telemetry-lake", "Unexpected lake owner")
    return session


def profile(session, bundle):
    from decimal import Decimal

    table = session.resource("dynamodb").Table("af-dev-arquifusion")
    key = {"PK": "MACHINE#" + MACHINE, "SK": "PROFILE"}
    item = key | {
        "machineId": MACHINE,
        "operationId": OPERATION,
        "model": "AF-BRIDGE-DEMO",
        "machineType": "EXCAVATOR",
        "status": "OPERATING",
        "zoneId": "PASCO",
        "lat": Decimal("-10.684"),
        "lon": Decimal("-76.263"),
        "GSI1PK": "OP#" + OPERATION + "#MACHINE",
        "GSI1SK": "OPERATING#" + MACHINE,
        "lastSeen": bundle["rows"][0]["timestamp"],
        "bridgeRun": bundle["run"],
    }
    previous = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if previous is None:
        require(
            not table.query(
                KeyConditionExpression="PK = :pk", ExpressionAttributeValues={":pk": key["PK"]}, Limit=1
            ).get("Items"),
            "Synthetic partition already used",
        )
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    else:
        require(previous == item, "Existing synthetic machine belongs to another run")


def send(session, bundle, index):
    row = bundle["rows"][index]
    marker = OUT / f"send-{index + 1}.json"
    if marker.exists():
        print(marker.read_text(encoding="utf-8"))
        return
    if index:
        require((OUT / "found-1.json").exists(), "Verify the first S3 object before submitting the second reading")
    profile(session, bundle)
    fire = session.client("firehose", config=Config(retries={"total_max_attempts": 1}))
    desc = fire.describe_delivery_stream(DeliveryStreamName="af-dev-telemetry")["DeliveryStreamDescription"]
    (dest,) = desc["Destinations"]
    require(
        desc["DeliveryStreamStatus"] == "ACTIVE"
        and dest["ExtendedS3DestinationDescription"]["BucketARN"] == "arn:aws:s3:::" + BUCKET,
        "Unexpected stream",
    )
    marker.write_text(json.dumps({"state": "submitting", "messageId": row["messageId"]}), encoding="utf-8")
    resp = fire.put_record(
        DeliveryStreamName="af-dev-telemetry", Record={"Data": (json.dumps(row, separators=(",", ":")) + "\n").encode()}
    )
    save(
        marker, {"state": "accepted", "messageId": row["messageId"], "requestId": resp["ResponseMetadata"]["RequestId"]}
    )
    print(marker.read_text(encoding="utf-8"))


def find_object(session, bundle, index):
    s3 = session.client("s3")
    scope = {"Bucket": BUCKET, "ExpectedBucketOwner": ACCOUNT}
    found = []
    row = bundle["rows"][index]
    # Only objects delivered after this rehearsal started can contain its newly generated message ID.
    since = datetime.fromisoformat(bundle["rows"][1]["timestamp"])
    for page in s3.get_paginator("list_objects_v2").paginate(**scope, Prefix="records/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < since - timedelta(seconds=5) or obj["Size"] > 4_000_000:
                continue
            resp = s3.get_object(**scope, Key=obj["Key"], IfMatch=obj["ETag"])
            with resp["Body"] as body:
                data = body.read()
            raw = pq.read_table(io.BytesIO(data), columns=["messageid", "machineid", "operationid"]).to_pylist()
            if any(v == {"messageid": row["messageId"], "machineid": MACHINE, "operationid": OPERATION} for v in raw):
                found.append(
                    {
                        "key": obj["Key"],
                        "etag": obj["ETag"].strip('"'),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "messageId": row["messageId"],
                        "rows": len(raw),
                        "observed": obj["LastModified"].isoformat(),
                    }
                )
    if not found:
        print("awaiting Firehose delivery")
        return
    require(len(found) == 1, "More than one object contains the test reading")
    save(OUT / f"found-{index + 1}.json", found[0])
    print(json.dumps(found[0]))


def replay(session, bundle):
    marker = OUT / "replay.json"
    if marker.exists():
        print(marker.read_text(encoding="utf-8"))
        return
    require((OUT / "found-2.json").exists(), "Verify both original objects before replay")
    original = json.loads((OUT / "found-1.json").read_text(encoding="utf-8"))
    head = session.client("s3").head_object(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT, Key=original["key"])
    require(
        head["ETag"].strip('"') == original["etag"] and original["messageId"] == bundle["rows"][0]["messageId"],
        "Source changed",
    )
    event = {
        "Records": [
            {
                "eventVersion": "2.1",
                "awsRegion": REGION,
                "eventSource": "aws:s3",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": BUCKET},
                    "object": {
                        "key": quote_plus(original["key"], safe="/"),
                        "eTag": original["etag"],
                        "size": original["bytes"],
                    },
                },
            }
        ]
    }
    save(marker, {"state": "submitting", "sha256": original["sha256"]})
    r = session.client("sqs", config=Config(retries={"total_max_attempts": 1})).send_message(
        QueueUrl=QUEUE, MessageBody=json.dumps(event)
    )
    save(marker, {"state": "accepted", "sqsMessageId": r["MessageId"], "sha256": original["sha256"]})
    print(marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "send-1", "send-2", "find-1", "find-2", "replay"))
    action = parser.parse_args().action
    data = prepare()
    if action == "prepare":
        print(data["run"], [r["messageId"] for r in data["rows"]])
    else:
        aws = cloud()
        if action.startswith("send"):
            send(aws, data, int(action[-1]) - 1)
        elif action.startswith("find"):
            find_object(aws, data, int(action[-1]) - 1)
        else:
            replay(aws, data)
