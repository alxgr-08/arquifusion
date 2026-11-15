import hashlib
import json
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError

from infra.aws.telemetry import run_case
from infra.aws.telemetry.prepare_bucket import ACCOUNT, REGION, TAGS
from infra.aws.telemetry.prepare_case import STREAM, prepare

BUCKET = f"af-dev-arquifusion-lake-{ACCOUNT}-{REGION}-an"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/LabRole"


@pytest.fixture
def lake():
    session = Mock()
    clients = {name: Mock() for name in ("sts", "firehose", "s3", "dynamodb")}
    session.client.side_effect = lambda name, **options: clients[name]
    clients["sts"].get_caller_identity.return_value = {
        "Account": ACCOUNT,
        "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/voclabs/operator",
    }
    clients["dynamodb"].query.return_value = {"Count": 0}
    tags = [{"Key": k, "Value": v} for k, v in TAGS.items()]
    clients["s3"].get_bucket_tagging.return_value = {"TagSet": tags}
    clients["s3"].get_bucket_notification_configuration.return_value = {}
    clients["firehose"].list_tags_for_delivery_stream.return_value = {"Tags": tags, "HasMoreTags": False}
    clients["firehose"].put_record.return_value = {"RecordId": "f-1", "ResponseMetadata": {"RequestId": "r-1"}}
    destination = {
        "BucketARN": f"arn:aws:s3:::{BUCKET}",
        "RoleARN": ROLE,
        "Prefix": "records/",
        "ErrorOutputPrefix": "errors/!{firehose:error-output-type}/",
        "DataFormatConversionConfiguration": {
            "Enabled": True,
            "SchemaConfiguration": {
                "CatalogId": ACCOUNT,
                "Region": REGION,
                "RoleARN": ROLE,
                "DatabaseName": "af_dev_telemetry",
                "TableName": "telemetry",
            },
            "OutputFormatConfiguration": {"Serializer": {"ParquetSerDe": {"Compression": "SNAPPY"}}},
        },
    }
    clients["firehose"].describe_delivery_stream.return_value = {
        "DeliveryStreamDescription": {
            "DeliveryStreamARN": f"arn:aws:firehose:{REGION}:{ACCOUNT}:deliverystream/{STREAM}",
            "DeliveryStreamStatus": "ACTIVE",
            "DeliveryStreamType": "DirectPut",
            "Destinations": [{"ExtendedS3DestinationDescription": destination}],
        }
    }
    return session, clients, destination


@pytest.mark.parametrize("outcome", ["accepted", "missing-id", "timeout"])
def test_one_host_put_and_journal_blocks_replay(tmp_path, lake, outcome):
    case = prepare(tmp_path)
    message, _, ndjson = run_case.check_case(case)
    session, clients, _ = lake
    put = clients["firehose"].put_record
    if outcome == "accepted":
        run_case.send_firehose_only(case, ACCOUNT, session)
    elif outcome == "missing-id":
        put.return_value.pop("RecordId")
        with pytest.raises(ValueError, match="acknowledge this record"):
            run_case.send_firehose_only(case, ACCOUNT, session)
    else:
        put.side_effect = ReadTimeoutError(endpoint_url="https://example.invalid")
        with pytest.raises(ReadTimeoutError):
            run_case.send_firehose_only(case, ACCOUNT, session)
    put.assert_called_once_with(DeliveryStreamName=STREAM, Record={"Data": ndjson})
    clients["s3"].get_bucket_tagging.assert_called_once_with(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)
    clients["s3"].get_bucket_notification_configuration.assert_called_once_with(
        Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT
    )
    assert all(call.kwargs["config"].retries["total_max_attempts"] == 1 for call in session.client.call_args_list)
    assert all(call.kwargs["config"].ignore_configured_endpoint_urls for call in session.client.call_args_list)
    journal = [json.loads(line) for line in (case / "dispatch.jsonl").read_text().splitlines()]
    assert journal[0] == {
        "event": "firehose-attempt",
        "messageId": message["messageId"],
        "sha256": hashlib.sha256(ndjson).hexdigest(),
    }
    if outcome == "accepted":
        assert journal[-1] == {"event": "firehose-accepted", "recordId": "f-1", "requestId": "r-1"}
    with pytest.raises(ValueError, match="dispatch already started"):
        run_case.send_firehose_only(case, ACCOUNT, session)
    assert put.call_count == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("BucketARN", "arn:aws:s3:::other"),
        ("ProcessingConfiguration", {"Enabled": True, "Processors": [{"Type": "Lambda"}]}),
        ("ProcessingConfiguration", {"Processors": [{"Type": "Lambda"}]}),
        ("S3BackupMode", "Enabled"),
        ("RoleARN", "another role"),
    ],
)
def test_wrong_or_lambda_backed_destination_prevents_dispatch(tmp_path, lake, key, value):
    case = prepare(tmp_path)
    session, clients, dest = lake
    dest[key] = value
    with pytest.raises(ValueError, match="approved regional telemetry lake"):
        run_case.send_firehose_only(case, ACCOUNT, session)
    clients["firehose"].put_record.assert_not_called()
    assert not (case / "dispatch.jsonl").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "session-account",
        "session-role",
        "stream-tags",
        "bucket-tags",
        "notification",
        "eventbridge",
        "collision",
        "bytes",
    ],
)
def test_fail_closed_before_send(tmp_path, lake, bad):
    case = prepare(tmp_path)
    session, clients, _ = lake
    if bad == "session-account":
        clients["sts"].get_caller_identity.return_value["Account"] = "000000000000"
    elif bad == "session-role":
        clients["sts"].get_caller_identity.return_value["Arn"] = "other"
    elif bad == "stream-tags":
        clients["firehose"].list_tags_for_delivery_stream.return_value["Tags"] = []
    elif bad == "bucket-tags":
        clients["s3"].get_bucket_tagging.return_value["TagSet"] = []
    elif bad in {"notification", "eventbridge"}:
        clients["s3"].get_bucket_notification_configuration.return_value = (
            {"LambdaFunctionConfigurations": [{"Id": "x"}]}
            if bad == "notification"
            else {"EventBridgeConfiguration": {}}
        )
    elif bad == "collision":
        clients["dynamodb"].query.return_value["Count"] = 1
    else:
        (case / "firehose-record.ndjson").write_bytes(b"{}\n")
    with pytest.raises(ValueError):
        run_case.send_firehose_only(case, ACCOUNT, session)
    clients["firehose"].put_record.assert_not_called()
    assert not (case / "dispatch.jsonl").exists()


@pytest.mark.parametrize(
    "flags",
    [
        ["--send"],
        ["--send", "--send-firehose-only"],
        ["--send-fire"],
        ["--send-firehose-only", "--account-id", "999999999999"],
    ],
)
def test_legacy_abbreviated_or_wrong_account_cli_never_connects(tmp_path, monkeypatch, flags):
    case = prepare(tmp_path)
    session = Mock()
    monkeypatch.setattr(boto3, "Session", session)
    monkeypatch.setattr(run_case, "CASES", tmp_path)
    with pytest.raises(SystemExit) as error:
        run_case.main([case.name, *flags])
    assert error.value.code == 2
    session.assert_not_called()
    assert not (case / "dispatch.jsonl").exists()


def test_offline_review_succeeds_with_no_session(tmp_path, monkeypatch):
    case = prepare(tmp_path)
    session = Mock()
    monkeypatch.setattr(boto3, "Session", session)
    monkeypatch.setattr(run_case, "CASES", tmp_path)
    assert run_case.main([case.name]) == 0
    session.assert_not_called()


def test_legacy_partial_dispatch_protects_the_same_case(tmp_path, lake):
    case = prepare(tmp_path)
    journal = case / "dispatch.jsonl"
    journal.write_text('{"event":"lambda-attempt"}\n', encoding="utf-8")
    session, _, _ = lake
    with pytest.raises(ValueError, match="dispatch already started"):
        run_case.send_firehose_only(case, ACCOUNT, session)
    session.client.assert_not_called()
    assert journal.read_text(encoding="utf-8") == '{"event":"lambda-attempt"}\n'
