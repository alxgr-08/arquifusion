"""Review one frozen simulator-direct reading offline. --send dispatches it once per target in Academy."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shared"))

from af_shared.keys import machine_pk  # noqa: E402

from infra.aws.telemetry.prepare_case import FUNCTION, STREAM, single_fleet, validate_reading  # noqa: E402

REGION = "us-east-1"
CASES = ROOT / "build" / "telemetry-cases"


def check_case(case: Path) -> tuple[dict, bytes, bytes]:
    if not re.fullmatch(r"[a-f0-9]{32}", case.name):
        raise ValueError("Expected the 32-character ID printed by prepare_case.py.")
    fleet = single_fleet(UUID(hex=case.name))
    if json.loads((case / "fleet.json").read_bytes()) != fleet:
        raise ValueError("The fleet must match this case's single synthetic machine.")
    machine = fleet["machines"][0]
    raw = (case / "reading.json").read_bytes()
    record = (case / "firehose-record.ndjson").read_bytes()
    message = json.loads(raw)
    validate_reading(message)
    if (
        message["machineId"] != machine["machineId"]
        or message["operationId"] != fleet["operationId"]
        or message["model"] != machine["model"]
        or message["location"]["zoneId"] != machine["zoneId"]
        or record != raw + b"\n"
        or b"\n" in raw
    ):
        raise ValueError("Both destinations must receive the same single, isolated simulator reading.")
    return message, raw, record


def send(case: Path, account: str, session) -> None:
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Supply the approved 12-digit Academy account.")
    message, raw, record = check_case(case)
    journal = case / "dispatch.jsonl"
    if journal.exists():
        raise ValueError("A dispatch already started. Reconcile dispatch.jsonl and the messageId before any replay.")
    options = Config(connect_timeout=5, read_timeout=15, retries={"mode": "standard", "total_max_attempts": 1})
    if session.client("sts", config=options).get_caller_identity()["Account"] != account:
        raise ValueError("The active Academy account differs from the approved account.")
    functions = session.client("lambda", config=options)
    firehose = session.client("firehose", config=options)
    arn = f"arn:aws:lambda:{REGION}:{account}:function:{FUNCTION}"
    tags = functions.list_tags(Resource=arn)["Tags"]
    if any(tags.get(k) != v for k, v in {"project": "arquifusion", "env": "dev", "owner": "rol1"}.items()):
        raise ValueError("Unexpected project, environment or owner on the evaluator.")
    stream = firehose.describe_delivery_stream(DeliveryStreamName=STREAM)["DeliveryStreamDescription"]
    dest = stream.get("Destinations", [])
    s3 = dest[0].get("ExtendedS3DestinationDescription", {}) if len(dest) == 1 else {}
    conversion = s3.get("DataFormatConversionConfiguration", {})
    schema = conversion.get("SchemaConfiguration", {})
    parquet = conversion.get("OutputFormatConfiguration", {}).get("Serializer", {}).get("ParquetSerDe", {})
    if (
        stream.get("DeliveryStreamARN") != f"arn:aws:firehose:{REGION}:{account}:deliverystream/{STREAM}"
        or stream.get("DeliveryStreamStatus") != "ACTIVE"
        or stream.get("DeliveryStreamType") != "DirectPut"
        or s3.get("BucketARN") != f"arn:aws:s3:::af-dev-arquifusion-lake-{account}-{REGION}-an"
        or s3.get("Prefix") != "records/"
        or s3.get("ErrorOutputPrefix") != "errors/!{firehose:error-output-type}/"
        or conversion.get("Enabled") is not True
        or schema.get("CatalogId") != account
        or schema.get("DatabaseName") != "af_dev_telemetry"
        or schema.get("TableName") != "telemetry"
        or parquet.get("Compression") != "SNAPPY"
    ):
        raise ValueError("The active Firehose destination does not match the approved regional telemetry lake.")
    rows = session.client("dynamodb", config=options).query(
        TableName="af-dev-arquifusion",
        KeyConditionExpression="PK = :machine",
        ExpressionAttributeValues={":machine": {"S": machine_pk(message["machineId"])}},
        ConsistentRead=True,
        Select="COUNT",
        Limit=1,
    )
    if rows["Count"] != 0:
        raise ValueError("The synthetic machine already has rows in the Academy table.")

    with journal.open("x", encoding="utf-8") as receipts:

        def note(event, **details):
            receipts.write(json.dumps({"event": event, **details}) + "\n")
            receipts.flush()
            os.fsync(receipts.fileno())

        note("lambda-attempt", messageId=message["messageId"], sha256=hashlib.sha256(raw).hexdigest())
        accepted = functions.invoke(FunctionName=arn, InvocationType="Event", Payload=raw)
        if body := accepted.get("Payload"):
            body.close()
        if accepted.get("StatusCode") != 202:
            raise ValueError("Lambda did not acknowledge the asynchronous invocation.")
        note("lambda-accepted", requestId=accepted["ResponseMetadata"]["RequestId"])
        note("firehose-attempt")
        response = firehose.put_record_batch(DeliveryStreamName=STREAM, Records=[{"Data": record}])
        entries = response.get("RequestResponses", [])
        entry = entries[0] if len(entries) == 1 else {}
        if response.get("FailedPutCount") != 0 or entry.get("ErrorCode") or not entry.get("RecordId"):
            note(
                "firehose-unconfirmed",
                errorCode=entry.get("ErrorCode"),
                requestId=response["ResponseMetadata"]["RequestId"],
            )
            raise ValueError("Firehose did not acknowledge this record. Check the journal before any replay.")
        note("firehose-accepted", recordId=entry["RecordId"], requestId=response["ResponseMetadata"]["RequestId"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id", help="ID of an existing case under build/telemetry-cases.")
    parser.add_argument("--send", action="store_true", help="Send this one reading to the approved Academy targets.")
    parser.add_argument("--account-id", help="Explicit, approved AWS Academy account for --send.")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.case_id):
        parser.error("Pass the case ID, not an arbitrary path.")
    case = CASES / args.case_id
    if args.send and not re.fullmatch(r"[0-9]{12}", args.account_id or ""):
        parser.error("--send requires an explicitly approved --account-id.")
    try:
        if args.send:
            import boto3

            send(case, args.account_id, boto3.Session(profile_name="default", region_name=REGION))
            print(f"Both services accepted the record. Evidence: {case / 'dispatch.jsonl'}")
        else:
            message, raw, _ = check_case(case)
            print(
                f"Offline | {message['machineId']} | {message['messageId']} | SHA-256 {hashlib.sha256(raw).hexdigest()}"
            )
    except (OSError, KeyError, ValueError, ValidationError, BotoCoreError, ClientError) as exc:
        print(f"Case stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
