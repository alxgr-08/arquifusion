import json
from unittest.mock import Mock

import pytest

from infra.aws.telemetry.prepare_case import prepare
from infra.aws.telemetry.run_case import FUNCTION, REGION, STREAM, check_case, send

ACCOUNT = "123456789012"


def session_for_test():
    session = Mock()
    clients = {name: Mock() for name in ("sts", "lambda", "firehose", "dynamodb")}
    session.client.side_effect = lambda name, **options: clients[name]
    clients["sts"].get_caller_identity.return_value = {"Account": ACCOUNT}
    clients["lambda"].list_tags.return_value = {"Tags": {"project": "arquifusion", "env": "dev", "owner": "rol1"}}
    clients["lambda"].invoke.return_value = {"StatusCode": 202, "ResponseMetadata": {"RequestId": "lambda-request"}}
    clients["dynamodb"].query.return_value = {"Count": 0}
    clients["firehose"].describe_delivery_stream.return_value = {
        "DeliveryStreamDescription": {
            "DeliveryStreamARN": f"arn:aws:firehose:{REGION}:{ACCOUNT}:deliverystream/{STREAM}",
            "DeliveryStreamStatus": "ACTIVE",
            "DeliveryStreamType": "DirectPut",
            "Destinations": [
                {
                    "ExtendedS3DestinationDescription": {
                        "BucketARN": f"arn:aws:s3:::af-dev-arquifusion-lake-{ACCOUNT}-{REGION}-an",
                        "Prefix": "records/",
                        "ErrorOutputPrefix": "errors/!{firehose:error-output-type}/",
                        "DataFormatConversionConfiguration": {
                            "Enabled": True,
                            "SchemaConfiguration": {
                                "CatalogId": ACCOUNT,
                                "DatabaseName": "af_dev_telemetry",
                                "TableName": "telemetry",
                            },
                            "OutputFormatConfiguration": {"Serializer": {"ParquetSerDe": {"Compression": "SNAPPY"}}},
                        },
                    }
                }
            ],
        }
    }
    return session, clients


@pytest.mark.parametrize("success", [True, False])
def test_send_one_record_per_destination_and_do_not_repeat(tmp_path, success):
    case = prepare(tmp_path)
    message, raw, ndjson = check_case(case)
    session, clients = session_for_test()
    clients["firehose"].put_record_batch.return_value = {
        "FailedPutCount": 0 if success else 1,
        "RequestResponses": [{"RecordId": "f-1"} if success else {"ErrorCode": "ServiceUnavailableException"}],
        "ResponseMetadata": {"RequestId": "firehose-request"},
    }
    if success:
        send(case, ACCOUNT, session)
    else:
        with pytest.raises(ValueError, match="acknowledge this record"):
            send(case, ACCOUNT, session)
    clients["lambda"].invoke.assert_called_once_with(
        FunctionName=f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}",
        InvocationType="Event",
        Payload=raw,
    )
    clients["firehose"].put_record_batch.assert_called_once_with(DeliveryStreamName=STREAM, Records=[{"Data": ndjson}])
    journal = [json.loads(line) for line in (case / "dispatch.jsonl").read_text().splitlines()]
    assert journal[0]["messageId"] == message["messageId"]
    assert journal[-1]["event"] == ("firehose-accepted" if success else "firehose-unconfirmed")
    with pytest.raises(ValueError, match="dispatch already started"):
        send(case, ACCOUNT, session)


def test_wrong_account_collision_or_modified_case_prevent_sending(tmp_path):
    case = prepare(tmp_path)
    session, clients = session_for_test()
    with pytest.raises(ValueError, match="differs from the approved account"):
        send(case, "000000000000", session)
    clients["dynamodb"].query.return_value = {"Count": 1}
    with pytest.raises(ValueError, match="already has rows"):
        send(case, ACCOUNT, session)
    (case / "firehose-record.ndjson").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="same single"):
        send(case, ACCOUNT, session)
    clients["lambda"].invoke.assert_not_called()
    clients["firehose"].put_record_batch.assert_not_called()


def test_destination_mismatch_stops_before_evaluator(tmp_path):
    case = prepare(tmp_path)
    session, clients = session_for_test()
    stream = clients["firehose"].describe_delivery_stream.return_value["DeliveryStreamDescription"]
    stream["Destinations"][0]["ExtendedS3DestinationDescription"]["BucketARN"] = "arn:aws:s3:::wrong-bucket"
    with pytest.raises(ValueError, match="approved regional telemetry lake"):
        send(case, ACCOUNT, session)
    clients["lambda"].invoke.assert_not_called()
    clients["firehose"].put_record_batch.assert_not_called()
