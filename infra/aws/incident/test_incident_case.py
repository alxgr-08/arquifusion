"""The review case must finish without allocating real stock or technicians."""

import json
import sys
from copy import deepcopy
from datetime import datetime

import pytest
from af_shared import config, keys
from boto3.dynamodb.types import TypeDeserializer

from infra.aws.incident import incident_catalog, prepare_case
from infra.aws.incident.test_catalog import PARAMETER, catalog_manifest


@pytest.mark.parametrize("with_part", [False, True], ids=["reviewed-catalog", "isolated-test-stock"])
def test_isolated_incident_reaches_done(tmp_path, monkeypatch, with_part):
    monkeypatch.syspath_prepend(str(prepare_case.ROOT / "statemachines" / "tests"))
    from asl_runner import Runner
    from flow_harness import Harness, ack_all, definition

    case = prepare_case.prepare_case(tmp_path)
    envelope = json.loads((case / "envelope.json").read_text(encoding="utf-8"))
    harness = Harness()
    harness.clock.now = datetime.fromisoformat(envelope["detail"]["detectedAt"])
    existing = deepcopy(harness.table.items)
    for op in json.loads((case / "seed.json").read_text(encoding="utf-8"))["TransactItems"]:
        assert op["Put"]["ConditionExpression"] == "attribute_not_exists(PK)"
        harness.table.put_item(Item=TypeDeserializer().deserialize({"M": op["Put"]["Item"]}))

    fleet = harness.ssm.json(config.FLEET_CONFIG)
    part, warehouse = f"PART-TEST-{case.name}", f"WH-TEST-{case.name}"
    stock_key = keys.stock_keys(part, warehouse)
    if with_part:
        fleet["warehouses"][warehouse] = {"zones": [envelope["detail"]["zoneId"]]}
        fleet["parts"].append({"partId": part, "name": "Test", "component": "DRIVETRAIN", "stock": {warehouse: 2}})
        harness.table.put_item(Item={**stock_key, "qty": 2, "available": 2, "reserved": 0, "reservations": {}})

    ref, _ = catalog_manifest(fleet, "inline")
    harness.ssm.put_parameter(Name=PARAMETER, Value=json.dumps(ref))
    del harness.ssm.params[config.FLEET_CONFIG]
    store = incident_catalog.IncidentParameters(PARAMETER, client=harness.ssm)
    monkeypatch.setenv("AF_INCIDENT_CATALOG", PARAMETER)
    monkeypatch.setitem(sys.modules, "handler", harness.tasks)
    monkeypatch.setattr(incident_catalog, "_runtime", lambda: (harness.table, store))
    monkeypatch.setattr(incident_catalog, "now_iso", harness.clock)
    tasks = harness.functions()
    tasks["${incident_tasks_arn}"] = lambda payload: incident_catalog.handler(payload, None)
    ack = ack_all(arrived_after=1, resolved_after=1, harness=harness)

    def confirm(stage):
        response = ack(stage)
        if with_part:
            stock = harness.table.get(stock_key["PK"], stock_key["SK"])
            incident_id = stage["payload"]["incident"]["incidentId"]
            assert (stock["qty"], stock["available"], stock["reserved"]) == (2, 1, 1)
            assert stock["reservations"] == {incident_id: 1}
        if response["event"] == "RESOLVED":
            response["partsUsed"] = [{"partId": part, "qty": 1}] if with_part else []
        return response

    runner = Runner(definition("incident"), tasks, confirm)
    runner.run(envelope)
    incident = harness.incident(envelope["detail"]["machineId"])
    assert runner.visited[-1] == "Done"
    assert incident["state"] == "RESOLVED"
    assert incident["aiDiagnosis"]["component"] == "DRIVETRAIN"
    assert incident["assignedTech"] == f"TEST-{case.name}"
    expected_parts = [{"partId": part, "warehouse": warehouse, "qty": 1}] if with_part else []
    assert incident["partsReserved"] == incident["partsUsed"] == expected_parts
    if with_part:
        stock = harness.table.get(stock_key["PK"], stock_key["SK"])
        assert (stock["qty"], stock["available"], stock["reserved"], stock["reservations"]) == (1, 1, 0, {})
    assert ("INCIDENT", "TECHNICIAN", "EMAIL") in harness.published()
    assert {"NOTIFIED", "CLOSED"} <= {entry["event"] for entry in incident["activity"]}
    technician = harness.table.get(f"TECH#TEST-{case.name}", "PROFILE")
    assert (technician["availability"], technician["queue"]) == ("AVAILABLE", 0)
    assert "openIncidentId" not in harness.table.get(f"MACHINE#{envelope['detail']['machineId']}", "PROFILE")
    assert all(harness.table.items[key] == item for key, item in existing.items())
