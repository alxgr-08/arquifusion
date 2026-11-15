import json

import pytest
from af_shared import config, grants
from af_shared.fakes import FakeSsm, FakeTable, demo_fleet_raw

ISS = "https://accounts.google.com"


def event(body: dict, sub: str = "sup-1") -> dict:
    claims = {"iss": ISS, "sub": sub}
    return {"body": json.dumps(body), "requestContext": {"authorizer": {"jwt": {"claims": claims}}}}


@pytest.fixture(autouse=True)
def granted_table(monkeypatch, load_handler):
    table = FakeTable()
    table.put_item(Item=grants.item(ISS, "sup-1", "MINA-NORTE", "SUPERVISOR"))
    table.put_item(Item=grants.item(ISS, "tech-1", "MINA-NORTE", "TECHNICIAN", tech_id="T-07"))
    table.put_item(Item=grants.item(ISS, "other-op", "MINA-SUR", "SUPERVISOR"))
    h = load_handler("fault_injector")
    monkeypatch.setattr(h, "_table", lambda: table)
    return table


def setup(load_handler):
    h = load_handler("fault_injector")
    ssm = FakeSsm({config.FLEET_CONFIG: demo_fleet_raw()})
    return h, ssm, config.ParamStore(client=ssm)


def test_only_granted_roles_of_the_same_operation_can_inject(load_handler) -> None:
    h, _, store = setup(load_handler)
    body = {"machineId": "EXC-0042", "faultType": "BEARING_WEAR"}
    assert h.handle({"body": json.dumps(body)}, store)["statusCode"] == 401
    assert h.handle(event(body, sub="nobody"), store)["statusCode"] == 403
    assert h.handle(event(body, sub="tech-1"), store)["statusCode"] == 403
    assert h.handle(event(body, sub="other-op"), store)["statusCode"] == 403


def test_inject_machine_fault_as_the_frontend_sends_it(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    kicked = []
    res = h.handle(event({"machineId": "EXC-0042", "faultType": "BEARING_WEAR"}), store, kick=lambda: kicked.append(1))
    assert res["statusCode"] == 201 and kicked == [1]
    faults = ssm.json(config.SIM_FAULTS)["faults"]
    assert [(f["machineId"], f["faultType"], f["intensity"]) for f in faults] == [("EXC-0042", "BEARING_WEAR", 1.0)]


def test_fuel_batch_zone_hits_every_machine_in_the_target_zone(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    res = h.handle(event({"machineId": "EXC-0042", "faultType": "FUEL_BATCH_ZONE"}), store)
    body = json.loads(res["body"])
    assert sorted(body["affectedMachines"]) == ["EXC-0042", "EXC-0051", "LDR-0012", "TRK-0103", "TRK-0107"]
    assert {f["zoneId"] for f in ssm.json(config.SIM_FAULTS)["faults"]} == {"Z3"}


def test_reinject_replaces_and_clear_removes(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEATING"}), store)
    h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEATING", "intensity": 2}), store)
    assert [f["intensity"] for f in ssm.json(config.SIM_FAULTS)["faults"]] == [2.0]
    res = h.handle(event({"action": "CLEAR"}), store)
    assert json.loads(res["body"])["cleared"] == 1 and ssm.json(config.SIM_FAULTS)["faults"] == []


def test_validation_and_auth_errors(load_handler) -> None:
    h, _, store = setup(load_handler)
    assert h.handle(event({"machineId": "EXC-0042", "faultType": "NOPE"}), store)["statusCode"] == 400
    assert h.handle(event({"machineId": "XXX-9999", "faultType": "OVERHEATING"}), store)["statusCode"] == 404
    assert h.handle(event({"faultType": "OVERHEATING"}), store)["statusCode"] == 400
    forbidden = h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEATING"}, sub="tech-1"), store)
    assert forbidden["statusCode"] == 403 and json.loads(forbidden["body"])["error"]["code"] == "FORBIDDEN"
