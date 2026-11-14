"""API tests, including the technician ack that resumes the real incident Step Function."""

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from af_shared.fakes import Recorder
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "statemachines" / "tests"))
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T16:00:00Z"


def req(
    method: str,
    path: str,
    groups: str = "[SUPERVISOR]",
    query: dict | None = None,
    body: dict | None = None,
    claims: dict | None = None,
) -> dict:
    return {
        "rawPath": path,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "http": {"method": method},
            "stage": "$default",
            "authorizer": {"jwt": {"claims": {"cognito:groups": groups, **(claims or {})}}},
        },
    }


@pytest.fixture
def api(load_handler):
    return load_handler("api")


def call(api, h: Harness, sfn, *args, **kwargs) -> tuple[int, dict]:
    res = api.route(req(*args, **kwargs), h.table, sfn, NOW)
    return res["statusCode"], json.loads(res["body"])


class StepFunctionsViaApi:
    """on_wait handler: the technician presses the buttons in the web, which calls POST .../ack."""

    def __init__(self, api, h: Harness) -> None:
        self.api, self.h = api, h
        self.sfn = Recorder()
        self.responses: list[tuple[int, dict]] = []

    def __call__(self, info: dict) -> dict:
        inc = info["payload"]["incident"]
        event = "ARRIVED" if info["payload"]["stage"] == "ARRIVAL" else "RESOLVED"
        body = {"event": event, "notes": "ok"}
        if event == "RESOLVED":
            body["partsUsed"] = [{"partId": "BRG-KIT-02", "qty": 1}]
        path = f"/machines/{inc['machineId']}/incidents/{inc['incidentId']}/ack"
        status, out = call(self.api, self.h, self.sfn, "POST", path, groups="[TECHNICIAN]", body=body)
        self.responses.append((status, out))
        assert status == 200, out
        sent = self.sfn.of("send_task_success")[-1]
        assert sent["taskToken"] == info["token"]
        return json.loads(sent["output"])


def test_technician_acks_drive_the_step_function_to_closed(api) -> None:
    h = Harness()
    driver = StepFunctionsViaApi(api, h)
    runner, _ = h.run_incident(anomaly_event(), driver)
    assert runner.visited[-1] == "Done"
    assert [r[1]["state"] for r in driver.responses] == ["IN_PROGRESS", "RESOLVED"]
    inc = h.incident("EXC-0042")
    assert inc["state"] == "CLOSED"
    events = [e["event"] for e in inc["timeline"]]
    assert events.index("ARRIVED") < events.index("RESOLVED") < events.index("CLOSED")
    assert "taskToken" not in inc


def test_ack_rejects_wrong_stage_duplicates_and_expired_tokens(api) -> None:
    h = Harness()
    sfn = Recorder()
    from asl_runner import Paused

    def hold(_info):
        raise Paused()

    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    inc = h.incident("EXC-0042")
    path = f"/machines/EXC-0042/incidents/{inc['incidentId']}/ack"
    status, body = call(api, h, sfn, "POST", path, groups="[TECHNICIAN]", body={"event": "RESOLVED"})
    assert status == 409 and body["error"]["code"] == "WRONG_STAGE"
    assert call(api, h, sfn, "POST", path, groups="[OPERATOR]", body={"event": "ARRIVED"})[0] == 403
    other = {"custom:techId": "T-01"}
    assert call(api, h, sfn, "POST", path, groups="[TECHNICIAN]", body={"event": "ARRIVED"}, claims=other)[0] == 403
    assert call(api, h, sfn, "POST", path, groups="[TECHNICIAN]", body={"event": "NOPE"})[0] == 400
    assert (
        call(
            api,
            h,
            sfn,
            "POST",
            "/machines/EXC-0042/incidents/INC-zzzz/ack",
            groups="[TECHNICIAN]",
            body={"event": "ARRIVED"},
        )[0]
        == 404
    )

    expired = Recorder(
        {
            "send_task_success": lambda **kw: (_ for _ in ()).throw(
                ClientError({"Error": {"Code": "TaskTimedOut", "Message": "x"}}, "SendTaskSuccess")
            )
        }
    )
    status, body = call(api, h, expired, "POST", path, groups="[MAINT_LEAD]", body={"event": "ARRIVED"})
    assert status == 409 and body["error"]["code"] == "TASK_EXPIRED"
    status, body = call(api, h, sfn, "POST", path, groups="[TECHNICIAN]", body={"event": "ARRIVED"})
    assert status == 409 and body["error"]["code"] in ("ALREADY_CONFIRMED", "WRONG_STAGE")


def test_read_endpoints(api) -> None:
    h = Harness()
    sfn = Recorder()
    from asl_runner import Paused

    def hold(_info):
        raise Paused()

    with pytest.raises(Paused):
        h.run_incident(anomaly_event(severity="CRITICAL"), hold)
    for minute in range(3):
        ts = f"2026-11-13T15:5{minute}:00Z"
        h.table.put_item(
            Item={
                "PK": "MACHINE#EXC-0042",
                "SK": f"READING#{ts}",
                "timestamp": ts,
                "metrics": {"temperatureC": Decimal("92.4")},
                "expiresAt": 1,
            }
        )
    h.table.put_item(
        Item={
            "PK": "MACHINE#EXC-0042",
            "SK": "KPI#2026-11-12",
            "GSI1PK": "OP#MINA-NORTE#KPI#2026-11-12",
            "GSI1SK": "EXC-0042",
            "utilization": Decimal("0.81"),
        }
    )
    h.table.put_item(Item={"PK": "PART#BRG-KIT-02", "SK": "FORECAST#WH-SUR#2026-W47", "demand": 2})

    status, body = call(api, h, sfn, "GET", "/operations/MINA-NORTE/machines")
    assert status == 200 and body["count"] == 13 and {"lat", "lon", "status", "healthScore"} <= set(body["items"][0])
    status, body = call(api, h, sfn, "GET", "/operations/MINA-NORTE/machines", query={"status": "operating"})
    assert body["count"] == 13
    status, body = call(api, h, sfn, "GET", "/operations/MINA-NORTE/machines", query={"limit": "5"})
    assert body["count"] == 5 and body["nextToken"]
    status, page2 = call(
        api, h, sfn, "GET", "/operations/MINA-NORTE/machines", query={"limit": "5", "nextToken": body["nextToken"]}
    )
    assert {m["machineId"] for m in page2["items"]}.isdisjoint({m["machineId"] for m in body["items"]})

    status, machine = call(api, h, sfn, "GET", "/machines/EXC-0042")
    assert status == 200 and machine["openIncidentSeverity"] == "CRITICAL" and "PK" not in machine
    assert call(api, h, sfn, "GET", "/machines/NOPE-0000")[0] == 404

    status, readings = call(api, h, sfn, "GET", "/machines/EXC-0042/readings", query={"from": "2026-11-13T15:50:30Z"})
    assert [r["timestamp"] for r in readings["items"]] == ["2026-11-13T15:51:00Z", "2026-11-13T15:52:00Z"]
    assert "expiresAt" not in readings["items"][0]

    status, incidents = call(api, h, sfn, "GET", "/operations/MINA-NORTE/incidents")
    assert incidents["count"] == 1 and incidents["items"][0]["severity"] == "CRITICAL"
    assert "taskToken" not in incidents["items"][0] and "anomaly" not in incidents["items"][0]
    status, history = call(api, h, sfn, "GET", "/machines/EXC-0042/incidents")
    assert history["items"][0]["assignedTech"]

    status, techs = call(api, h, sfn, "GET", "/operations/MINA-NORTE/technicians", query={"availability": "ASSIGNED"})
    assert [t["techId"] for t in techs["items"]] == [history["items"][0]["assignedTech"]]

    status, stock = call(api, h, sfn, "GET", "/parts/BRG-KIT-02/stock")
    sur = next(w for w in stock["warehouses"] if w["warehouseId"] == "WH-SUR")
    assert (sur["available"], sur["reserved"], sur["forecast"][0]["week"]) == (0, 1, "2026-W47")
    assert call(api, h, sfn, "GET", "/parts/NOPE/stock")[0] == 404

    status, kpis = call(api, h, sfn, "GET", "/operations/MINA-NORTE/kpis")
    assert kpis["date"] == "2026-11-12" and kpis["items"][0]["utilization"] == 0.81
    assert call(api, h, sfn, "GET", "/operations/MINA-NORTE/kpis", groups="[OPERATOR]")[0] == 403


def test_errors_auth_and_cors(api) -> None:
    h = Harness()
    sfn = Recorder()
    assert call(api, h, sfn, "GET", "/nope")[0] == 404
    assert call(api, h, sfn, "DELETE", "/machines/EXC-0042")[0] == 405
    assert call(api, h, sfn, "GET", "/machines/EXC-0042", groups="")[0] == 401
    assert call(api, h, sfn, "GET", "/machines/EXC-0042/readings", query={"from": "yesterday"})[0] == 400
    res = api.route(req("OPTIONS", "/machines/EXC-0042"), h.table, sfn, NOW)
    assert res["statusCode"] == 204 and res["headers"]["Access-Control-Allow-Origin"] == "*"
