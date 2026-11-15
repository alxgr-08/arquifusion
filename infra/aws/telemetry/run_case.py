"""Review a frozen reading offline. --send-firehose-only submits one record from this host."""

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

from infra.aws.telemetry.prepare_bucket import ACCOUNT, REGION, TAGS  # noqa: E402
from infra.aws.telemetry.prepare_case import STREAM, single_fleet, validate_reading  # noqa: E402

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
        raise ValueError("The two frozen files must contain the same single, isolated simulator reading.")
    return message, raw, record


def verify_lake(firehose, s3_client, account: str) -> None:
    bucket = f"af-dev-arquifusion-lake-{account}-{REGION}-an"
    role = f"arn:aws:iam::{account}:role/LabRole"
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
        or stream.get("HasMoreDestinations")
        or s3.get("BucketARN") != f"arn:aws:s3:::{bucket}"
        or s3.get("RoleARN") != role
        or s3.get("S3BackupMode", "Disabled") != "Disabled"
        or ("ProcessingConfiguration" in s3 and s3["ProcessingConfiguration"].get("Enabled") is not False)
        or s3.get("Prefix") != "records/"
        or s3.get("ErrorOutputPrefix") != "errors/!{firehose:error-output-type}/"
        or conversion.get("Enabled") is not True
        or schema.get("CatalogId") != account
        or schema.get("Region") != REGION
        or schema.get("RoleARN") != role
        or schema.get("DatabaseName") != "af_dev_telemetry"
        or schema.get("TableName") != "telemetry"
        or parquet.get("Compression") != "SNAPPY"
    ):
        raise ValueError("The active Firehose destination does not match the approved regional telemetry lake.")
    result = firehose.list_tags_for_delivery_stream(DeliveryStreamName=STREAM, Limit=50)
    stream_tags = {tag["Key"]: tag.get("Value") for tag in result["Tags"]}
    if result.get("HasMoreTags") or any(stream_tags.get(key) != TAGS[key] for key in ("project", "env", "owner")):
        raise ValueError("Unexpected ownership tags on the delivery stream.")
    target = {"Bucket": bucket, "ExpectedBucketOwner": account}
    bucket_tags = {tag["Key"]: tag["Value"] for tag in s3_client.get_bucket_tagging(**target)["TagSet"]}
    if not TAGS.items() <= bucket_tags.items():
        raise ValueError("Unexpected ownership tags on the lake bucket.")
    notifications = s3_client.get_bucket_notification_configuration(**target)
    if any(
        notifications.get(key) for key in ("LambdaFunctionConfigurations", "TopicConfigurations", "QueueConfigurations")
    ) or ("EventBridgeConfiguration" in notifications):
        raise ValueError("The dedicated lake must have no downstream notification triggers for this isolated dispatch.")


def send_firehose_only(case: Path, account: str, session) -> None:
    if account != ACCOUNT:
        raise ValueError("Supply the approved ArquiFusion Academy account.")
    message, _, record = check_case(case)
    journal = case / "dispatch.jsonl"
    if journal.exists():
        raise ValueError("A dispatch already started. Reconcile dispatch.jsonl and the messageId before any replay.")
    options = Config(
        connect_timeout=5,
        read_timeout=15,
        ignore_configured_endpoint_urls=True,
        retries={"mode": "standard", "total_max_attempts": 1},
    )
    caller = session.client("sts", config=options).get_caller_identity()
    if caller.get("Account") != account or not caller.get("Arn", "").startswith(
        f"arn:aws:sts::{account}:assumed-role/voclabs/"
    ):
        raise ValueError("Unexpected Academy account or session role.")
    firehose = session.client("firehose", config=options)
    verify_lake(firehose, session.client("s3", config=options), account)
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

        note("firehose-attempt", messageId=message["messageId"], sha256=hashlib.sha256(record).hexdigest())
        response = firehose.put_record(DeliveryStreamName=STREAM, Record={"Data": record})
        if not response.get("RecordId"):
            note("firehose-unconfirmed", requestId=response["ResponseMetadata"]["RequestId"])
            raise ValueError("Firehose did not acknowledge this record. Check the journal before any replay.")
        note("firehose-accepted", recordId=response["RecordId"], requestId=response["ResponseMetadata"]["RequestId"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("case_id", help="ID of an existing case under build/telemetry-cases.")
    parser.add_argument("--send", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--send-firehose-only", action="store_true", help="Submit a single record directly to Firehose."
    )
    parser.add_argument("--account-id", help="Approved Academy account for --send-firehose-only.")
    args = parser.parse_args(argv)
    if args.send:
        parser.error("The legacy --send is retired. This command cannot send telemetry through Lambda.")
    if not re.fullmatch(r"[a-f0-9]{32}", args.case_id):
        parser.error("Pass the case ID, not an arbitrary path.")
    case = CASES / args.case_id
    if args.send_firehose_only and args.account_id != ACCOUNT:
        parser.error("--send-firehose-only requires the approved Academy --account-id.")
    try:
        if args.send_firehose_only:
            import boto3

            send_firehose_only(case, args.account_id, boto3.Session(profile_name="default", region_name=REGION))
            print(f"Firehose accepted one record. Evidence: {case / 'dispatch.jsonl'}")
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
