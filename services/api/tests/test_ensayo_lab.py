"""Dry run of the AWS Academy rehearsal (docs/ensayo-lab): the exact files Rol 1 will use, through the real
Step Function definition, the real Lambdas and the API Lambda invoked directly (no API Gateway yet)."""

import json
import sys
from pathlib import Path

import pytest
from af_shared import config
from af_shared.fakes import Recorder
from boto3.dynamodb.types import TypeDeserializer

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "docs" / "ensayo-lab"
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
from flow_harness import Harness  # noqa: E402

NOW = "2026-11-16T15:30:00Z"


def lab(name: str) -> dict:
    return json.loads((LAB / name).read_text(encoding="utf-8"))


def put(h: Harness, name: str) -> None:
    d = TypeDeserializer()
    h.table.put_item(Item={k: d.deserialize(v) for k, v in lab(name).items()})


def shared_stock(h: Harness) -> dict:
    return {
        k: (v["available"], v["reserved"])
        for k, v in h.table.items.items()
        if k[0].startswith("PART#") and k[1].startswith("STOCK#") and k[1] != "STOCK#WH-ENSAYO"
    }


class Operator:
    """One operator answers both waits by invoking af-dev-api with the files in docs/ensayo-lab."""

    def __init__(self, api, h: Harness) -> None:
        self.api, self.h, self.sfn = api, h, Recorder()
        self.answers: list[tuple[int, dict]] = []

    def __call__(self, info: dict) -> dict:
        inc = info["payload"]["incident"]
        name = "api-ack-arrived.json" if info["payload"]["stage"] == "ARRIVAL" else "api-ack-resolved.json"
        event = lab(name)
        event["rawPath"] = event["rawPath"].replace("INC-XXXX", inc["incidentId"])
        res = self.api.route(event, self.h.table, self.sfn, NOW)
        self.answers.append((res["statusCode"], json.loads(res["body"])))
        assert res["statusCode"] == 200, res["body"]
        return json.loads(self.sfn.of("send_task_success")[-1]["output"])


@pytest.fixture
def api(load_handler):
    return load_handler("api")


def lab_harness(case2: bool = False) -> Harness:
    h = Harness()
    put(h, "item-machine.json")
    put(h, "item-technician.json")
    put(h, "item-grant-operador.json")
    h.ssm.params[config.INCIDENT_SETTINGS] = json.dumps(lab("incident-settings-lab.json"))
    if case2:
        put(h, "item-stock-caso2.json")
        fleet = h.ssm.json(config.FLEET_CONFIG)
        fleet["warehouses"]["WH-ENSAYO"] = {"zones": ["ZE"]}
        h.ssm.params[config.FLEET_CONFIG] = json.dumps(fleet)
    return h


def assert_released(h: Harness) -> None:
    profile = h.table.get_item(Key={"PK": "MACHINE#EXC-E001", "SK": "PROFILE"})["Item"]
    assert "openIncidentId" not in profile and "openIncidentExecution" not in profile
    tech = h.table.get_item(Key={"PK": "TECH#T-E1", "SK": "PROFILE"})["Item"]
    assert tech["availability"] == "AVAILABLE" and int(tech["queue"]) == 0


def test_case1_vibration_warning_reaches_resolved_without_touching_shared_stock(api) -> None:
    h = lab_harness()
    before = shared_stock(h)
    op = Operator(api, h)
    runner, _ = h.run_incident(lab("anomaly-caso1-vibracion.json"), op)
    assert runner.visited[-1] == "Done"
    inc = h.incident("EXC-E001")
    assert inc["assignedTech"] == "T-E1" and inc["state"] == "RESOLVED"
    assert inc["partsReserved"] == []
    assert [e["event"] for e in inc["timeline"]] == ["DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED"]
    assert [a[1]["state"] for a in op.answers] == ["IN_PROGRESS", "RESOLVED"]
    assert shared_stock(h) == before
    assert_released(h)


def test_case2_bearing_consumes_only_the_lab_warehouse(api) -> None:
    h = lab_harness(case2=True)
    before = shared_stock(h)
    runner, _ = h.run_incident(lab("anomaly-caso2-rodamiento.json"), Operator(api, h))
    assert runner.visited[-1] == "Done"
    inc = h.incident("EXC-E001")
    assert inc["partsReserved"] == [{"partId": "BRG-SPH-22", "warehouse": "WH-ENSAYO", "qty": 1}]
    stock = h.table.get_item(Key={"PK": "PART#BRG-SPH-22", "SK": "STOCK#WH-ENSAYO"})["Item"]
    assert (int(stock["qty"]), int(stock["available"]), int(stock["reserved"])) == (0, 0, 0)
    assert shared_stock(h) == before
    maint = [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#EXC-E001" and sk.startswith("MAINT#")]
    assert len(maint) == 1
    assert_released(h)
