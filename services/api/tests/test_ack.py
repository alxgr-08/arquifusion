"""Issue #42: permissions of POST .../ack, repeated or lost callbacks, and a close that leaves exactly one
maintenance record and one stock movement."""

import json
import sys
from pathlib import Path

import incidents
import pytest
from af_shared import grants
from af_shared.fakes import Recorder
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
from asl_runner import Paused  # noqa: E402
from auth import as_user  # noqa: E402
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T16:00:00Z"


def hold(_info):
    raise Paused()


def error(code: str):
    def fail(**_kw):
        raise ClientError({"Error": {"Code": code, "Message": code}}, "SendTaskSuccess")

    return fail


def post(
    api,
    h: Harness,
    sfn,
    action: str,
    role: str = "TECHNICIAN",
    tech: str | None = "T-07",
    operation: str | None = "MINA-NORTE",
    machine: str = "EXC-0042",
    body: dict | None = None,
):
    inc = h.incident(machine)
    claims = as_user(h.table, role, operation, tech)
    event = {
        "rawPath": f"/machines/{machine}/incidents/{inc['incidentId']}/ack",
        "body": json.dumps(body or {"action": action}),
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"jwt": {"claims": claims}}},
    }
    res = api.route(event, h.table, sfn, NOW)
    return res["statusCode"], json.loads(res["body"])


@pytest.fixture
def api(load_handler):
    return load_handler("api")


@pytest.fixture
def waiting() -> Harness:
    """EXC-0042 with an open incident assigned to T-07, waiting for ARRIVED."""
    h = Harness()
    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    assert h.incident("EXC-0042")["assignedTech"] == "T-07"
    return h


# ------------------------------------------------------------------------------------------- permissions
@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({"role": "OPERATOR", "tech": None}, 403, "FORBIDDEN"),
        ({"tech": "T-03"}, 403, "NOT_ASSIGNED"),
        ({"tech": None}, 403, "NO_TECH_ID"),
        ({"operation": None}, 403, "NO_GRANT"),
        ({"operation": "MINA-SUR"}, 403, "OTHER_OPERATION"),
        ({"role": "MAINT_LEAD", "tech": None, "operation": "MINA-SUR"}, 403, "OTHER_OPERATION"),
    ],
)
def test_only_the_assigned_technician_or_a_lead_of_the_same_operation(api, waiting, kwargs, status, code) -> None:
    sfn = Recorder()
    got, body = post(api, waiting, sfn, "ARRIVED", **kwargs)
    assert (got, body["error"]["code"]) == (status, code)
    assert sfn.of("send_task_success") == []


def test_lead_of_the_same_operation_can_confirm_for_the_technician(api, waiting) -> None:
    sfn = Recorder()
    status, body = post(api, waiting, sfn, "ARRIVED", role="MAINT_LEAD", tech=None)
    assert status == 200 and body["state"] == "IN_PROGRESS"
    assert len(sfn.of("send_task_success")) == 1


def test_wrong_stage_and_bad_input(api, waiting) -> None:
    sfn = Recorder()
    status, body = post(api, waiting, sfn, "RESOLVED")
    assert (status, body["error"]["code"]) == (409, "WRONG_STAGE")
    assert post(api, waiting, sfn, "NOPE")[0] == 400


# ------------------------------------------------------------------------------------------- repeated / lost
def test_repeated_click_returns_the_incident_without_calling_step_functions_again(api, waiting) -> None:
    sfn = Recorder()
    first = post(api, waiting, sfn, "ARRIVED")
    second = post(api, waiting, sfn, "ARRIVED")
    assert first[0] == second[0] == 200 and second[1]["state"] == "IN_PROGRESS"
    assert len(sfn.of("send_task_success")) == 1
    events = [e["event"] for e in waiting.incident("EXC-0042")["timeline"]]
    assert events.count("ARRIVED") == 1


def test_step_functions_unreachable_then_retry_delivers_the_same_callback(api, waiting) -> None:
    status, body = post(api, waiting, Recorder({"send_task_success": error("ThrottlingException")}), "ARRIVED")
    assert (status, body["error"]["code"]) == (503, "CALLBACK_PENDING")
    assert "ARRIVED" not in [e["event"] for e in waiting.incident("EXC-0042")["timeline"]]
    sfn = Recorder()
    status, body = post(api, waiting, sfn, "ARRIVED")
    assert status == 200 and body["state"] == "IN_PROGRESS"
    (call,) = sfn.of("send_task_success")
    assert json.loads(call["output"])["event"] == "ARRIVED"
    assert "pendingAck" not in waiting.incident("EXC-0042") and "taskToken" not in waiting.incident("EXC-0042")


def test_answer_lost_after_delivery_is_recognised_on_retry(api, waiting) -> None:
    # 1st request: Step Functions accepts, but our Lambda dies before finishing (response lost).
    status, _ = post(api, waiting, Recorder({"send_task_success": error("ServiceUnavailable")}), "ARRIVED")
    assert status == 503
    # Meanwhile the execution moved on (the callback had actually arrived) and armed the next wait.
    incidents.store_task_token(
        waiting.table,
        {
            "incident": {**waiting.incident("EXC-0042"), "incidentSK": waiting.incident("EXC-0042")["SK"]},
            "taskToken": "token-next",
            "stage": "RESOLUTION",
        },
        NOW,
    )
    # 2nd request: the old token is rejected, but the backend sees the flow already advanced.
    status, body = post(api, waiting, Recorder({"send_task_success": error("TaskTimedOut")}), "ARRIVED")
    assert status == 200 and body["state"] == "IN_PROGRESS"
    inc = waiting.incident("EXC-0042")
    assert inc["taskToken"] == "token-next" and inc["pendingStage"] == "RESOLUTION"  # next wait untouched
    assert [e["event"] for e in inc["timeline"]].count("ARRIVED") == 1


def test_expired_wait_is_reported_and_not_retried_forever(api, waiting) -> None:
    import assignment

    # 1st click: Step Functions unreachable, the callback stays pending.
    assert post(api, waiting, Recorder({"send_task_success": error("ThrottlingException")}), "ARRIVED")[0] == 503
    # Meanwhile the wait timed out: the flow released T-07 and assigned T-03.
    inc = waiting.incident("EXC-0042")
    payload = {
        "incident": {**inc, "incidentSK": inc["SK"], "settings": {}},
        "assignment": {"techId": "T-07", "excluded": []},
    }
    assignment.release(waiting.table, payload, NOW)
    waiting.table.update_item(
        Key={"PK": inc["PK"], "SK": inc["SK"]},
        UpdateExpression="SET assignedTech = :t",
        ExpressionAttributeValues={":t": "T-03"},
    )
    # The lead retries T-07's confirmation: the old token is dead and the incident has another technician.
    status, body = post(
        api,
        waiting,
        Recorder({"send_task_success": error("TaskTimedOut")}),
        "ARRIVED",
        role="MAINT_LEAD",
        tech=None,
    )
    assert (status, body["error"]["code"]) == (409, "TASK_EXPIRED")
    assert "pendingAck" not in waiting.incident("EXC-0042")
    # and T-07 can no longer confirm at all
    assert post(api, waiting, Recorder(), "ARRIVED")[1]["error"]["code"] == "NOT_ASSIGNED"


def test_resolution_re_armed_after_long_downtime_gets_the_callback(api, waiting) -> None:
    sfn = Recorder()
    assert post(api, waiting, sfn, "ARRIVED")[0] == 200
    inc = waiting.incident("EXC-0042")
    incidents.store_task_token(
        waiting.table,
        {"incident": {**inc, "incidentSK": inc["SK"]}, "taskToken": "token-r1", "stage": "RESOLUTION"},
        NOW,
    )
    # The RESOLVED click reaches us just as the long-downtime timeout re-armed the wait with a new token.
    calls = []

    def first_old_then_new(**kw):
        calls.append(kw["taskToken"])
        if kw["taskToken"] == "token-r1":
            incidents.store_task_token(
                waiting.table,
                {"incident": {**inc, "incidentSK": inc["SK"]}, "taskToken": "token-r2", "stage": "RESOLUTION"},
                NOW,
            )
            raise ClientError({"Error": {"Code": "TaskTimedOut", "Message": "x"}}, "SendTaskSuccess")
        return {}

    status, body = post(api, waiting, Recorder({"send_task_success": first_old_then_new}), "RESOLVED")
    assert status == 200 and calls == ["token-r1", "token-r2"] and body["state"] == "RESOLVED"


# ------------------------------------------------------------------------------------------- one close
def test_full_flow_with_double_clicks_closes_once(api) -> None:
    h = Harness()
    sfn_calls: list[str] = []

    def technician(info):
        action = "ARRIVED" if info["payload"]["stage"] == "ARRIVAL" else "RESOLVED"
        sfn = Recorder()
        for _ in range(3):  # impatient technician
            status, _ = post(api, h, sfn, action)
            assert status == 200
        sfn_calls.extend(c["taskToken"] for c in sfn.of("send_task_success"))
        return json.loads(sfn.of("send_task_success")[0]["output"])

    runner, _ = h.run_incident(anomaly_event(), technician)
    assert runner.visited[-1] == "Done"
    assert len(sfn_calls) == 2  # one callback per stage, never more
    maint = [k for k in h.table.items if k[0] == "MACHINE#EXC-0042" and k[1].startswith("MAINT#")]
    assert len(maint) == 1
    stock = h.table.get("PART#BRG-SPH-22", "STOCK#WH-NORTE")
    assert (stock["qty"], stock["available"], stock["reserved"]) == (1, 1, 0)


def test_close_retried_by_step_functions_does_not_duplicate_anything() -> None:
    h = Harness()
    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    row = h.incident("EXC-0042")
    payload = {
        "incident": {
            "incidentId": row["incidentId"],
            "incidentSK": row["SK"],
            "machineId": "EXC-0042",
            "operationId": "MINA-NORTE",
            "zoneId": "Z3",
            "detectedAt": row["detectedAt"],
            "createdAt": row["createdAt"],
            "settings": {},
        },
        "resolution": {"event": "RESOLVED"},
    }  # no "at": the date comes from `now`
    first = incidents.close_incident(h.table, payload, "2026-11-13T23:59:50Z")
    # Retry after midnight, with a different parts report: must change nothing.
    h.table.update_item(
        Key={"PK": row["PK"], "SK": row["SK"]},
        UpdateExpression="SET GSI1PK = :o",
        ExpressionAttributeValues={":o": "OP#MINA-NORTE#INCIDENT#OPEN"},
    )  # simulate crash before
    retry_payload = {**payload, "resolution": {"event": "RESOLVED", "partsUsed": []}}
    second = incidents.close_incident(h.table, retry_payload, "2026-11-14T00:00:30Z")
    assert first["maintenanceId"] == second["maintenanceId"]
    maint = [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#EXC-0042" and sk.startswith("MAINT#")]
    assert len(maint) == 1 and maint[0]["date"] == "2026-11-13T23:59:50Z"
    assert maint[0]["partsReplaced"] == ["BRG-SPH-22"]
    stock = h.table.get("PART#BRG-SPH-22", "STOCK#WH-NORTE")
    assert (stock["qty"], stock["available"], stock["reserved"], stock["reservations"]) == (1, 1, 0, {})
    assert h.table.get("TECH#T-07", "PROFILE")["queue"] == 0


# ------------------------------------------------------------------------------------------- grants (#42)
def test_revoked_grant_cannot_confirm(api) -> None:
    h = Harness()
    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    claims = as_user(h.table, "TECHNICIAN", "MINA-NORTE", "T-07")
    h.table.delete_item(Key=grants.grant_key("accounts.google.com", claims["sub"], "MINA-NORTE"))
    inc = h.incident("EXC-0042")
    event = {
        "rawPath": f"/machines/EXC-0042/incidents/{inc['incidentId']}/ack",
        "body": json.dumps({"action": "ARRIVED"}),
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"jwt": {"claims": claims}}},
    }
    sfn = Recorder()
    res = api.route(event, h.table, sfn, NOW)
    assert res["statusCode"] == 403 and json.loads(res["body"])["error"]["code"] == "NO_GRANT"
    assert sfn.of("send_task_success") == []


def test_grant_changed_or_reassigned_while_confirming_is_rejected_atomically(api, waiting, monkeypatch) -> None:
    routes = sys.modules["routes"]
    real = routes._authorize_ack

    def bump_version_meanwhile(ctx, incident):
        who, grant = real(ctx, incident)
        waiting.table.update_item(
            Key=grant.key, UpdateExpression="SET version = :v", ExpressionAttributeValues={":v": 2}
        )
        return who, grant

    monkeypatch.setattr(routes, "_authorize_ack", bump_version_meanwhile)
    sfn = Recorder()
    status, body = post(api, waiting, sfn, "ARRIVED")
    assert (status, body["error"]["code"]) == (403, "GRANT_CHANGED")
    assert "pendingAck" not in waiting.incident("EXC-0042") and sfn.of("send_task_success") == []

    def reassign_meanwhile(ctx, incident):
        who, grant = real(ctx, incident)
        inc = waiting.incident("EXC-0042")
        waiting.table.update_item(
            Key={"PK": inc["PK"], "SK": inc["SK"]},
            UpdateExpression="SET assignedTech = :t",
            ExpressionAttributeValues={":t": "T-03"},
        )
        return who, grant

    monkeypatch.setattr(routes, "_authorize_ack", reassign_meanwhile)
    status, body = post(api, waiting, sfn, "ARRIVED")
    assert (status, body["error"]["code"]) == (403, "NOT_ASSIGNED")
    assert "pendingAck" not in waiting.incident("EXC-0042") and sfn.of("send_task_success") == []


def test_reads_are_limited_to_the_operations_of_the_grant(api, waiting) -> None:
    claims = as_user(waiting.table, "SUPERVISOR", "MINA-SUR")
    for path in ("/operations/MINA-NORTE/machines", "/machines/EXC-0042", "/machines/EXC-0042/incidents"):
        event = {
            "rawPath": path,
            "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": claims}}},
        }
        res = api.route(event, waiting.table, Recorder(), NOW)
        assert res["statusCode"] == 403, path
    mine = as_user(waiting.table, "SUPERVISOR", "MINA-NORTE")
    event = {
        "rawPath": "/machines/EXC-0042",
        "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": mine}}},
    }
    assert api.route(event, waiting.table, Recorder(), NOW)["statusCode"] == 200
