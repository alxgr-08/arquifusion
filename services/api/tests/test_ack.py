"""POST .../ack (#42, review of #47): grants, the version of the wait (ETag / If-Match), a durable receipt per
incident + stage + generation, recovery of lost answers from the workflow's certification, and a close that can
be interrupted and resumed."""

import json
import sys
from pathlib import Path

import incidents
import pytest
from af_shared import grants
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
from asl_runner import Paused  # noqa: E402
from auth import as_user  # noqa: E402
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T16:00:00Z"
EXECUTION = "arn:aws:states:us-east-1:000000000000:execution:af-dev-incident:ack-tests"


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "SendTaskSuccess")


class Workflow:
    """Stand-in for Step Functions + the incident state machine after a wait.

    mode: "up" (accept), "down" (unreachable, nothing happens), "lost" (accepts, then the answer is lost).
    Accepting does what the ASL does: CertifyArrival + register WaitForResolution, or CloseIncident.
    """

    def __init__(self, h: Harness, inc: dict) -> None:
        self.h, self.inc, self.mode = h, inc, "up"
        self.calls: list[str] = []
        self.entered = 0

    def register(self, stage: str, token: str, tech: str | None = None, entered: str | None = None) -> None:
        self.entered += 1
        incidents.store_task_token(
            self.h.table,
            {
                "incident": self.inc,
                "stage": stage,
                "taskToken": token,
                "executionId": EXECUTION,
                "enteredTime": entered or f"2026-11-13T17:00:{self.entered:02d}.000Z",
                "techId": tech or self.h.incident(self.inc["machineId"]).get("assignedTech"),
            },
            NOW,
        )

    def send_task_success(self, taskToken: str, output: str) -> dict:  # noqa: N803
        self.calls.append(taskToken)
        if self.mode == "down":
            raise client_error("ThrottlingException")
        row = self.h.incident(self.inc["machineId"])
        if taskToken != row.get("taskToken"):
            raise client_error("InvalidToken")
        ack = json.loads(output)
        if row["pendingStage"] == "ARRIVAL":
            incidents.certify_ack(
                self.h.table, {"incident": self.inc, "stage": "ARRIVAL", "ack": ack, "executionId": EXECUTION}, NOW
            )
            self.register("RESOLUTION", f"{taskToken}-next")
        else:
            incidents.close_incident(
                self.h.table, {"incident": self.inc, "resolution": ack, "executionId": EXECUTION}, NOW
            )
        if self.mode == "lost":
            raise client_error("InternalFailure")
        return {}


def request(api, h: Harness, method: str, path: str, claims: dict | None, body=None, if_match: str | None = None):
    event = {
        "rawPath": path,
        "headers": {"if-match": f'"{if_match}"'} if if_match else {},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": claims}} if claims else {}},
    }
    return api.route(event, h.table, None, NOW) if method == "GET" else event


class Web:
    """The web: reads the incident (ETag) and posts the ack with If-Match, as Alexis' client will."""

    def __init__(self, api, h: Harness, workflow: Workflow) -> None:
        self.api, self.h, self.wf = api, h, workflow

    def path(self) -> str:
        inc = self.h.incident(self.wf.inc["machineId"])
        return f"/machines/{inc['machineId']}/incidents/{inc['incidentId']}"

    def etag(self, claims: dict | None = None) -> str | None:
        claims = claims or as_user(self.h.table, "SUPERVISOR")
        res = request(self.api, self.h, "GET", self.path(), claims)
        assert res["statusCode"] == 200, res["body"]
        tag = res["headers"].get("ETag")
        return tag.strip('"') if tag else None

    def post(
        self,
        action: str = "ARRIVED",
        role: str = "TECHNICIAN",
        tech: str | None = "T-07",
        operation: str | None = "MINA-NORTE",
        body: dict | None = None,
        if_match: str | None = "auto",
    ):
        claims = as_user(self.h.table, role, operation, tech)
        tag = self.etag() if if_match == "auto" else if_match
        event = request(self.api, self.h, "POST", self.path() + "/ack", claims, body or {"action": action}, tag)
        res = self.api.route(event, self.h.table, self.wf, NOW)
        return res["statusCode"], json.loads(res["body"]), res["headers"]


@pytest.fixture
def api(load_handler):
    return load_handler("api")


def paused_at_arrival() -> tuple[Harness, dict]:
    h = Harness()
    seen = {}

    def hold(info):
        seen.update(info["payload"])
        raise Paused()

    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    assert h.incident("EXC-0042")["assignedTech"] == "T-07"
    return h, seen["incident"]


@pytest.fixture
def web(api) -> Web:
    h, inc = paused_at_arrival()
    wf = Workflow(h, inc)
    wf.register("ARRIVAL", "token-a1", "T-07")  # the wait as the real ASL registers it (execution + EnteredTime)
    return Web(api, h, wf)


def events(web: Web) -> list[str]:
    return [e["event"] for e in web.h.incident("EXC-0042")["timeline"]]


def receipts(web: Web) -> list[dict]:
    return [i for (pk, sk), i in web.h.table.items.items() if sk.startswith("ACK#")]


# ------------------------------------------------------------------------------------ identity and operation
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
def test_rejections_leave_data_and_callback_intact(web, kwargs, status, code) -> None:
    before = web.h.incident("EXC-0042")
    got, body, _ = web.post(**kwargs)
    assert (got, body["error"]["code"]) == (status, code)
    assert web.wf.calls == [] and receipts(web) == []
    assert web.h.incident("EXC-0042") == before


def test_lead_confirms_for_the_technician_and_the_real_actor_is_kept(web) -> None:
    status, body, _ = web.post(role="MAINT_LEAD", tech=None)
    assert status == 200 and body["state"] == "IN_PROGRESS"
    (receipt,) = receipts(web)
    assert receipt["actor"]["role"] == "MAINT_LEAD" and receipt["status"] in ("ACCEPTED", "DELIVERED")
    certified = web.h.incident("EXC-0042")["certified"]["ARRIVAL"]
    assert certified["ackId"] == receipt["ackId"] and certified["actor"]["role"] == "MAINT_LEAD"
    assert events(web).count("ARRIVED") == 1 and web.h.incident("EXC-0042")["timeline"][-1]["tech"] == "T-07"


def test_revoked_grant_or_version_change_during_the_click(web, monkeypatch) -> None:
    routes = sys.modules["routes"]
    real = routes._authorize_ack

    def change_grant_meanwhile(ctx, incident):
        who, grant = real(ctx, incident)
        bump = {"UpdateExpression": "SET version = :v", "ExpressionAttributeValues": {":v": 2}}
        web.h.table.update_item(Key=grant.key, **bump)
        return who, grant

    monkeypatch.setattr(routes, "_authorize_ack", change_grant_meanwhile)
    assert web.post()[1]["error"]["code"] == "GRANT_CHANGED"

    def revoke_meanwhile(ctx, incident):
        who, grant = real(ctx, incident)
        web.h.table.delete_item(Key=grant.key)
        return who, grant

    monkeypatch.setattr(routes, "_authorize_ack", revoke_meanwhile)
    assert web.post()[1]["error"]["code"] == "GRANT_CHANGED"

    def reassign_meanwhile(ctx, incident):
        who, grant = real(ctx, incident)
        inc = web.h.incident("EXC-0042")
        web.h.table.update_item(
            Key={"PK": inc["PK"], "SK": inc["SK"]},
            UpdateExpression="SET assignedTech = :t",
            ExpressionAttributeValues={":t": "T-03"},
        )
        return who, grant

    monkeypatch.setattr(routes, "_authorize_ack", reassign_meanwhile)
    assert web.post()[1]["error"]["code"] == "NOT_ASSIGNED"
    assert web.wf.calls == [] and receipts(web) == [] and "ARRIVED" not in events(web)


def test_revoked_grant_is_rejected_on_the_next_request(api, web) -> None:
    claims = as_user(web.h.table, "TECHNICIAN", "MINA-NORTE", "T-07")
    web.h.table.delete_item(Key=grants.grant_key("accounts.google.com", claims["sub"], "MINA-NORTE"))
    event = request(api, web.h, "POST", web.path() + "/ack", claims, {"action": "ARRIVED"}, web.etag())
    res = api.route(event, web.h.table, web.wf, NOW)
    assert res["statusCode"] == 403 and json.loads(res["body"])["error"]["code"] == "NO_GRANT"
    assert web.wf.calls == []


# ------------------------------------------------------------------------------------ version of the wait
def test_if_match_is_required_and_must_be_the_current_wait(web) -> None:
    status, body, _ = web.post(if_match=None)
    assert (status, body["error"]["code"]) == (428, "PRECONDITION_REQUIRED")
    status, body, headers = web.post(if_match="not-this-one")
    assert (status, body["error"]["code"]) == (412, "STALE_WAIT") and headers["ETag"] == f'"{web.etag()}"'
    status, body, _ = web.post("RESOLVED")
    assert (status, body["error"]["code"]) == (409, "WRONG_STAGE")
    assert web.wf.calls == [] and receipts(web) == []


def test_same_wait_keeps_its_generation_and_a_late_older_registration_is_ignored(web) -> None:
    first = web.etag()
    inc = web.h.incident("EXC-0042")
    web.wf.register("ARRIVAL", "token-a1-retry", entered=inc["waitEnteredAt"])  # Lambda retry of the same wait
    assert web.etag() == first and web.h.incident("EXC-0042")["taskToken"] == "token-a1-retry"
    web.wf.register("ARRIVAL", "token-old", entered="2026-11-13T16:59:59.000Z")  # late, older wait
    assert web.etag() == first and web.h.incident("EXC-0042")["taskToken"] == "token-a1-retry"


def test_reassignment_to_the_same_technician_is_a_new_version(web, load_handler) -> None:
    seen = web.etag()
    assign = sys.modules.get("assignment") or __import__("assignment")
    assign.release(web.h.table, {"incident": web.wf.inc, "assignment": {"techId": "T-07"}}, NOW)  # tech timed out
    inc = web.h.incident("EXC-0042")
    web.h.table.update_item(
        Key={"PK": inc["PK"], "SK": inc["SK"]},
        UpdateExpression="SET assignedTech = :t",
        ExpressionAttributeValues={":t": "T-07"},
    )  # reassigned: same technician comes back
    web.wf.register("ARRIVAL", "token-a2", "T-07")
    assert web.etag() != seen
    status, body, _ = web.post(if_match=seen)  # the button the technician saw before the reassignment
    assert (status, body["error"]["code"]) == (412, "STALE_WAIT") and web.wf.calls == []
    assert web.post()[0] == 200 and web.wf.calls == ["token-a2"]


def test_wait_registered_before_v1_1_needs_reconciliation(web) -> None:
    inc = web.h.incident("EXC-0042")
    web.h.table.update_item(Key={"PK": inc["PK"], "SK": inc["SK"]}, UpdateExpression="REMOVE waitGeneration")
    status, body, _ = web.post(if_match="anything")
    assert (status, body["error"]["code"]) == (409, "WAIT_NEEDS_RECONCILIATION") and web.wf.calls == []


# ------------------------------------------------------------------------------------ double click and retries
def test_double_click_and_four_retries_are_one_effect(web) -> None:
    tag = web.etag()
    results = [web.post(if_match=tag) for _ in range(5)]
    assert [r[0] for r in results] == [200] * 5
    assert web.wf.calls == ["token-a1"] and len(receipts(web)) == 1 and events(web).count("ARRIVED") == 1


def test_other_parts_notes_or_actor_on_the_same_wait_is_a_conflict(web) -> None:
    web.wf.mode = "down"
    tag = web.etag()
    assert web.post(body={"action": "ARRIVED", "notes": "llegué"}, if_match=tag)[0] == 503
    status, body, _ = web.post(body={"action": "ARRIVED", "notes": "otra nota"}, if_match=tag)
    assert (status, body["error"]["code"]) == (409, "ACK_CONFLICT")
    status, body, _ = web.post(
        role="MAINT_LEAD", tech=None, body={"action": "ARRIVED", "notes": "llegué"}, if_match=tag
    )
    assert (status, body["error"]["code"]) == (409, "ACK_BY_OTHER")
    assert len(receipts(web)) == 1 and web.wf.calls == ["token-a1"]


# ------------------------------------------------------------------------------------ failure before / after
def test_unreachable_step_functions_then_the_client_retries_four_times(web) -> None:
    web.wf.mode = "down"
    tag = web.etag()
    for _ in range(3):
        status, body, headers = web.post(if_match=tag)
        assert (status, body["error"]["code"]) == (503, "CALLBACK_PENDING") and headers["Retry-After"] == "2"
    assert "ARRIVED" not in events(web)
    web.wf.mode = "up"
    status, body, _ = web.post(if_match=tag)
    assert status == 200 and body["state"] == "IN_PROGRESS"
    assert events(web).count("ARRIVED") == 1 and len(receipts(web)) == 1


def test_answer_lost_after_acceptance_is_recovered_and_the_next_wait_keeps_its_token(web) -> None:
    web.wf.mode = "lost"
    tag = web.etag()
    status, body, _ = web.post(if_match=tag)  # accepted, certified, next wait registered, answer lost
    assert status == 200 and body["state"] == "IN_PROGRESS"
    assert receipts(web)[0]["status"] == "ACCEPTED"
    inc = web.h.incident("EXC-0042")
    assert inc["pendingStage"] == "RESOLUTION" and inc["taskToken"] == "token-a1-next"
    web.wf.mode = "up"
    assert web.post(if_match=tag)[0] == 200  # the client repeats: no second callback
    assert web.wf.calls == ["token-a1"] and events(web).count("ARRIVED") == 1


def test_invalid_token_without_certification_is_never_taken_as_success(web) -> None:
    web.wf.mode = "down"
    tag = web.etag()
    assert web.post(if_match=tag)[0] == 503
    incidents.release_claim(web.h.table, {"incident": web.wf.inc, "error": {"Error": "Boom"}}, NOW)  # flow failed
    web.wf.mode = "up"
    status, body, _ = web.post(if_match=tag)
    assert (status, body["error"]["code"]) == (409, "TASK_EXPIRED")
    assert "ARRIVED" not in events(web) and receipts(web)[0]["status"] == "REJECTED"


def test_old_resolution_leaves_a_re_armed_wait_intact(web) -> None:
    assert web.post()[0] == 200  # ARRIVED -> WaitForResolution registered with token-a1-next
    web.wf.mode = "down"
    old = web.etag()
    assert web.post("RESOLVED", if_match=old)[0] == 503
    web.wf.register("RESOLUTION", "token-r2")  # long downtime: the workflow re-armed the wait
    web.wf.mode = "up"
    status, body, _ = web.post("RESOLVED", if_match=old)  # the client repeats the old request
    assert (status, body["error"]["code"]) == (409, "TASK_EXPIRED")
    inc = web.h.incident("EXC-0042")
    assert inc["taskToken"] == "token-r2" and "RESOLVED" not in events(web)
    assert web.post("RESOLVED", if_match=web.etag())[0] == 200  # a new decision on the new wait
    assert web.wf.calls[-1] == "token-r2" and events(web).count("RESOLVED") == 1


# ------------------------------------------------------------------------------------ close exactly once
def test_close_interrupted_after_closed_mark_is_resumed_the_next_day(web, monkeypatch) -> None:
    assert web.post()[0] == 200
    real = incidents.release_claim_only

    def crash_once(table, inc):
        monkeypatch.setattr(incidents, "release_claim_only", real)
        raise RuntimeError("Lambda timed out")

    monkeypatch.setattr(incidents, "release_claim_only", crash_once)
    status, body, _ = web.post("RESOLVED")  # accepted; then CloseIncident dies between #CLOSED and the release
    assert status == 200 and body["state"] == "RESOLVED"
    profile = web.h.table.get_item(Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"})["Item"]
    assert profile.get("openIncidentId") == web.wf.inc["incidentId"]  # still busy: this is what we fix

    out = incidents.close_incident(  # Step Functions retries CloseIncident (here: the next day)
        web.h.table,
        {"incident": web.wf.inc, "resolution": json.loads(receipts(web)[-1]["payload"]), "executionId": EXECUTION},
        "2026-11-14T09:00:00Z",
    )
    assert out["alreadyClosed"] is True
    profile = web.h.table.get_item(Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"})["Item"]
    assert "openIncidentId" not in profile
    maint = [i for (pk, sk), i in web.h.table.items.items() if pk == "MACHINE#EXC-0042" and sk.startswith("MAINT#")]
    assert len(maint) == 1
    stock = web.h.table.get_item(Key={"PK": "PART#BRG-SPH-22", "SK": "STOCK#WH-NORTE"})["Item"]
    assert (int(stock["qty"]), int(stock["available"]), int(stock["reserved"])) == (1, 1, 0)
    tech = web.h.table.get_item(Key={"PK": "TECH#T-07", "SK": "PROFILE"})["Item"]
    assert int(tech["queue"]) == 0 and tech["availability"] == "AVAILABLE"
    assert events(web).count("ARRIVED") == 1 and events(web).count("RESOLVED") == 1


def test_release_only_frees_the_machine_if_the_claim_is_still_ours(web) -> None:
    other = {**web.wf.inc, "incidentId": "INC-zzzz"}
    incidents.release_claim_only(web.h.table, other)
    profile = web.h.table.get_item(Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"})["Item"]
    assert profile["openIncidentId"] == web.wf.inc["incidentId"]


def test_two_concurrent_first_clicks_claim_the_same_receipt(web, monkeypatch) -> None:
    routes = sys.modules["routes"]
    web.wf.mode = "down"
    tag = web.etag()
    assert web.post(if_match=tag)[0] == 503  # first request stored the receipt
    real = routes._get_receipt
    calls = []

    def read_before_the_other_wrote(ctx, key):
        calls.append(key)
        return None if len(calls) == 1 else real(ctx, key)

    monkeypatch.setattr(routes, "_get_receipt", read_before_the_other_wrote)
    web.wf.mode = "up"
    status, body, _ = web.post(if_match=tag)  # its Put loses the race and continues with the stored receipt
    assert status == 200 and len(calls) == 2
    assert len(receipts(web)) == 1 and events(web).count("ARRIVED") == 1
