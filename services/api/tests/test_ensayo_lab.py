"""Dry run of the AWS Academy rehearsal (docs/ensayo-lab): the files scripts/ensayo_kit.py generates, through the
real Step Function definition, the real Lambdas and the API Lambda invoked directly (no API Gateway yet)."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from af_shared.fakes import Recorder
from boto3.dynamodb.types import TypeDeserializer

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))
import ensayo_kit  # noqa: E402
from flow_harness import Harness  # noqa: E402

NOW = "2026-11-16T15:30:00Z"
RUN = 7


@pytest.fixture
def kit() -> dict:
    return ensayo_kit.build(RUN, datetime(2026, 11, 16, 15, 0, tzinfo=UTC))


def put(h: Harness, item: dict) -> None:
    d = TypeDeserializer()
    h.table.put_item(
        Item={k: d.deserialize(v) for k, v in item.items()}, ConditionExpression="attribute_not_exists(PK)"
    )


def stock_outside_the_run(h: Harness) -> dict:
    return {
        k: (v["available"], v["reserved"])
        for k, v in h.table.items.items()
        if k[0].startswith("PART#") and k[1].startswith("STOCK#") and k[1] != "STOCK#WH-E0007"
    }


class Operator:
    """One operator answers both waits by invoking the API Lambda with the kit files: GET (ETag) then POST."""

    def __init__(self, api, h: Harness, kit: dict) -> None:
        self.api, self.h, self.kit, self.sfn = api, h, kit, Recorder()
        self.answers: list[tuple[int, dict]] = []
        self.timeouts: list[int] = []

    def __call__(self, info: dict) -> dict:
        self.timeouts.append(info["timeout"])
        inc = info["payload"]["incident"]
        get = json.loads(json.dumps(self.kit["api-get-incident.json"]).replace("INC-XXXX", inc["incidentId"]))
        seen = self.api.route(get, self.h.table, self.sfn, NOW)
        assert seen["statusCode"] == 200, seen["body"]
        name = "api-ack-arrived.json" if info["payload"]["stage"] == "ARRIVAL" else "api-ack-resolved.json"
        tag = seen["headers"]["ETag"].strip('"')
        post = json.dumps(self.kit[name]).replace("INC-XXXX", inc["incidentId"]).replace("ETAG-XXXX", tag)
        res = self.api.route(json.loads(post), self.h.table, self.sfn, NOW)
        self.answers.append((res["statusCode"], json.loads(res["body"])))
        assert res["statusCode"] == 200, res["body"]
        return json.loads(self.sfn.of("send_task_success")[-1]["output"])


@pytest.fixture
def api(load_handler):
    return load_handler("api")


def lab_harness(kit: dict, case2: bool = False) -> Harness:
    h = Harness()
    for name in ("item-op-settings.json", "item-machine.json", "item-technician.json", "item-grant-operador.json"):
        put(h, kit[name])
    if case2:
        put(h, kit["item-stock-caso2.json"])
    return h


def assert_released(h: Harness) -> None:
    profile = h.table.get_item(Key={"PK": "MACHINE#ENS-0007", "SK": "PROFILE"})["Item"]
    assert "openIncidentId" not in profile and "openIncidentExecution" not in profile
    tech = h.table.get_item(Key={"PK": "TECH#T-E0007", "SK": "PROFILE"})["Item"]
    assert tech["availability"] == "AVAILABLE" and int(tech["queue"]) == 0


def test_kit_ids_and_times_belong_to_the_run(kit) -> None:
    assert kit["run.json"]["operation"] == "ENSAYO-0007" and kit["run.json"]["machine"] == "ENS-0007"
    case1, case2 = kit["anomaly-caso1-vibracion.json"], kit["anomaly-caso2-rodamiento.json"]
    assert case1["time"] == case1["detail"]["detectedAt"] < case2["detail"]["detectedAt"]
    assert all(r["PK"] and r["SK"] for r in kit["run.json"]["rows"])


def test_case1_vibration_warning_resolves_and_a_resent_event_opens_nothing(api, kit) -> None:
    h = lab_harness(kit)
    before = stock_outside_the_run(h)
    params = dict(h.ssm.params)
    op = Operator(api, h, kit)
    runner, _ = h.run_incident(kit["anomaly-caso1-vibracion.json"], op)
    assert runner.visited[-1] == "Done"
    inc = h.incident("ENS-0007")
    assert inc["assignedTech"] == "T-E0007" and inc["state"] == "RESOLVED"
    assert op.timeouts == [600, 1800] and inc["partsReserved"] == []  # the run's own settings row
    assert [e["event"] for e in inc["timeline"]] == ["DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED"]
    assert [a[1]["state"] for a in op.answers] == ["IN_PROGRESS", "RESOLVED"]
    assert stock_outside_the_run(h) == before and h.ssm.params == params  # shared data and parameters intact
    assert_released(h)
    # The same event sent again (EventBridge redelivery or the #44 outbox): no second incident.
    runner, _ = h.run_incident(kit["anomaly-caso1-vibracion.json"], op)
    assert runner.visited[-1] == "DuplicateIgnored"
    rows = [k for k in h.table.items if k[0] == "MACHINE#ENS-0007" and k[1].startswith("INCIDENT#")]
    assert len(rows) == 1
    assert_released(h)


def test_case2_bearing_consumes_only_the_run_warehouse(api, kit) -> None:
    h = lab_harness(kit, case2=True)
    before = stock_outside_the_run(h)
    runner, _ = h.run_incident(kit["anomaly-caso2-rodamiento.json"], Operator(api, h, kit))
    assert runner.visited[-1] == "Done"
    inc = h.incident("ENS-0007")
    assert inc["partsReserved"] == [{"partId": "BRG-SPH-22", "warehouse": "WH-E0007", "qty": 1}]
    stock = h.table.get_item(Key={"PK": "PART#BRG-SPH-22", "SK": "STOCK#WH-E0007"})["Item"]
    assert (int(stock["qty"]), int(stock["available"]), int(stock["reserved"])) == (0, 0, 0)
    assert stock_outside_the_run(h) == before
    maint = [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#ENS-0007" and sk.startswith("MAINT#")]
    assert len(maint) == 1
    assert_released(h)
