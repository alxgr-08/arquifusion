import json

from af_shared import config
from af_shared.fakes import FakeSsm, demo_fleet_raw
from af_shared.fleet import SIMULATOR_KEYS, subset


def event(body: dict, groups: str = "[SUPERVISOR]") -> dict:
    return {"body": json.dumps(body), "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": groups}}}}}


def setup(load_handler):
    h = load_handler("fault_injector")
    ssm = FakeSsm({config.FLEET_CONFIG: subset(demo_fleet_raw(), SIMULATOR_KEYS)})
    return h, ssm, config.ParamStore(client=ssm)


def test_inject_machine_fault_and_kick_simulator(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    kicked = []
    body = {"machineId": "EXC-0042", "faultType": "BEARING_WEAR"}
    res = h.handle(event(body), store, kick=lambda: kicked.append(1))
    assert res["statusCode"] == 201 and kicked == [1]
    faults = ssm.json(config.SIM_FAULTS)["faults"]
    summary = [(f["machineId"], f["faultType"], f["intensity"]) for f in faults]
    assert summary == [("EXC-0042", "BEARING_WEAR", "WARNING")]


def test_zone_fault_hits_every_machine_in_zone(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    res = h.handle(event({"zoneId": "Z3", "faultType": "FUEL_CONTAMINATION"}), store)
    body = json.loads(res["body"])
    assert sorted(body["affectedMachines"]) == ["EXC-0042", "HTK-0103", "HTK-0104", "LDR-0202"]
    assert len(ssm.json(config.SIM_FAULTS)["faults"]) == 4


def test_reinject_replaces_and_clear_removes(load_handler) -> None:
    h, ssm, store = setup(load_handler)
    h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEAT"}), store)
    h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEAT", "intensity": "CRITICAL"}), store)
    assert [f["intensity"] for f in ssm.json(config.SIM_FAULTS)["faults"]] == ["CRITICAL"]
    res = h.handle(event({"action": "CLEAR"}), store)
    assert json.loads(res["body"])["cleared"] == 1 and ssm.json(config.SIM_FAULTS)["faults"] == []


def test_validation_and_auth_errors(load_handler) -> None:
    h, _, store = setup(load_handler)
    assert h.handle(event({"machineId": "EXC-0042", "faultType": "NOPE"}), store)["statusCode"] == 400
    assert h.handle(event({"machineId": "XXX-9999", "faultType": "OVERHEAT"}), store)["statusCode"] == 404
    assert h.handle(event({"faultType": "OVERHEAT"}), store)["statusCode"] == 400
    forbidden = h.handle(event({"machineId": "EXC-0042", "faultType": "OVERHEAT"}, groups="[OPERATOR]"), store)
    assert forbidden["statusCode"] == 403
    assert json.loads(forbidden["body"])["error"]["code"] == "FORBIDDEN"
