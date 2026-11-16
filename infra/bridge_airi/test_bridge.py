import base64
import hashlib
import json
from datetime import date
from unittest.mock import Mock

import pytest

from infra.bridge_airi import build_image, gateway, worker


def response(data, status=200):
    r = Mock(status_code=status, ok=status < 400)
    r.json.return_value = data
    r.text = json.dumps(data)
    r.content = r.text.encode()
    return r


def test_gateway_only_grants_named_methods_and_native_data_integrations():
    api = gateway.spec()
    assert set(api["paths"]) == {"/lease", "/complete", "/status", "/kpi", "/files/{year}/{month}/{day}/{hour}/{name}"}
    for routes in api["paths"].values():
        for method in routes.values():
            assert method["security"] == [{"googleMachine": []}]
            integration = method["x-amazon-apigateway-integration"]
            assert integration["type"] == "aws"
            assert "lambda" not in integration["uri"]
    text = api["paths"]["/kpi"]["post"]["x-amazon-apigateway-integration"]["requestTemplates"]["application/json"]
    assert '"ConditionCheck"' in text and "sourceDayReadings < :n" in text
    assert gateway.OP in text and gateway.TABLE in text


def test_cloud_build_context_exposes_only_three_files_and_fits_step_limits():
    spec = build_image.build_spec()
    files = json.loads("".join(spec["steps"][0]["args"][2:]))
    assert set(files) == {"Dockerfile", "requirements.txt", "worker.py"}
    assert all(base64.b64decode(data) == (build_image.HERE / name).read_bytes() for name, data in files.items())
    assert all(len(arg) <= 10000 for step in spec["steps"] for arg in step["args"])


def test_receive_xml_or_json_and_reject_unexpected_body():
    assert worker.queue_message(response({"Messages": [{"ReceiptHandle": "test", "Body": '{"Records": []}'}]})) == (
        "test",
        {"Records": []},
    )
    wrapped = {
        "ReceiveMessageResponse": {
            "ReceiveMessageResult": {"messages": [{"receiptHandle": "test", "body": '{"Records": []}'}]}
        }
    }
    assert worker.queue_message(response(wrapped)) == ("test", {"Records": []})
    with pytest.raises(ValueError, match="Unrecognized SQS"):
        worker.queue_message(response({"unexpected": []}))
    r = response({})
    r.content = b'<ReceiveMessageResponse xmlns="http://queue.amazonaws.com/doc/2012-11-05/"><ReceiveMessageResult/></ReceiveMessageResponse>'
    r.text = r.content.decode()
    assert worker.queue_message(r) is None
    r.text = "<Error/>"
    r.content = r.text.encode()
    with pytest.raises(ValueError, match="queue response"):
        worker.queue_message(r)


def test_native_s3_ranges_match_etag_and_both_parquet_boundaries(monkeypatch):
    data = b"PAR1abcdPAR1"
    blob = {
        "key": "records/2026/11/16/02/test.parquet",
        "size": len(data),
        "eTag": hashlib.md5(data, usedforsecurity=False).hexdigest(),
    }
    record = {
        "eventSource": "aws:s3",
        "eventName": "ObjectCreated:Put",
        "awsRegion": "us-east-1",
        "s3": {"bucket": {"name": worker.BUCKET}, "object": blob},
    }
    gw = object.__new__(worker.Gateway)
    monkeypatch.setattr(worker, "CHUNK", 6)

    def get(method, path, headers):
        first, last = map(int, headers["Range"][6:].split("-"))
        assert path == "files/2026/11/16/02/test.parquet" and method == "GET"
        return Mock(status_code=206, content=data[first : last + 1])

    gw.request = get
    assert gw.download(record) == data
    blob["eTag"] = "f" * 32
    with pytest.raises(ValueError, match="ETag mismatch"):
        gw.download(record)


def test_kpi_replay_verifies_before_writing_and_rejects_another_operation():
    row = {k: 0 for k in worker.METRICS.values()} | {
        "machine_id": "BRG-8115",
        "date": date(2026, 11, 16),
        "model": "AF-BRIDGE-DEMO",
        "last_message_id": "example",
        "source_readings": 2,
        "day_readings": 2,
        "fuel_anomaly": False,
    }
    item = {
        "sourceDayReadings": {"N": "2"},
        "operationId": {"S": "AF-BRIDGE-PERU"},
        "sourceProject": {"S": "airi-erp"},
        "sourceMessageId": {"S": "example"},
    }
    gw = object.__new__(worker.Gateway)
    gw.request = Mock(return_value=response({"Item": item}))
    assert gw.write_kpi(row) == "already_present"
    assert [c.args[1] for c in gw.request.call_args_list] == ["status"]
    item["operationId"]["S"] = "ANOTHER"
    with pytest.raises(ValueError, match="Foreign KPI"):
        gw.write_kpi(row)
