import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from af_shared import Telemetry, config
from af_shared.fakes import FakeSsm, Recorder, demo_fleet_raw
from af_shared.fleet import FleetConfig
from fleet_sim import FAULTS, ZONE_FAULTS, simulate
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts/telemetry.schema.json").read_text())
NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)
MACHINES = len(demo_fleet_raw()["machines"])


@pytest.fixture
def cfg() -> FleetConfig:
    return FleetConfig.model_validate(demo_fleet_raw())


def fault(machine: str, kind: str, started: datetime, intensity: float = 1.0) -> dict:
    return {
        "faultId": "F-1",
        "machineId": machine,
        "faultType": kind,
        "intensity": intensity,
        "startedAt": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def avg(cfg: FleetConfig, machine_id: str, metric: str, at: datetime, faults: list, ramp: float = 300) -> float:
    m = cfg.machine(machine_id)
    return sum(simulate(cfg, m, at + timedelta(seconds=s), faults, ramp)["metrics"][metric] for s in range(60)) / 60


def test_every_message_matches_the_contract(cfg: FleetConfig) -> None:
    validator = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for minute in range(0, 180, 7):
        for machine in cfg.machines:
            msg = simulate(cfg, machine, NOW + timedelta(minutes=minute), [])
            assert not list(validator.iter_errors(msg)), msg
            Telemetry.model_validate(msg)


def test_values_stay_around_the_model_normal(cfg: FleetConfig) -> None:
    for machine in cfg.machines:
        normal = cfg.model_of(machine.model).normal["pressureBar"]
        msg = simulate(cfg, machine, NOW, [])
        assert abs(msg["metrics"]["pressureBar"] - normal) / normal < 0.05


def test_same_inputs_same_message_so_retries_deduplicate(cfg: FleetConfig) -> None:
    m = cfg.machines[0]
    assert simulate(cfg, m, NOW, []) == simulate(cfg, m, NOW, [])
    assert simulate(cfg, m, NOW, [])["messageId"] != simulate(cfg, m, NOW + timedelta(seconds=10), [])["messageId"]


@pytest.mark.parametrize(
    ("kind", "metric", "delta"),
    [
        ("BEARING_WEAR", "temperatureC", 15),
        ("BEARING_WEAR", "vibrationMmS", 4.2),
        ("OVERHEATING", "temperatureC", 22),
        ("HYDRAULIC_LEAK", "pressureBar", -55),
    ],
)
def test_faults_match_the_frontend_mock_and_grow_gradually(
    cfg: FleetConfig, kind: str, metric: str, delta: float
) -> None:
    faults = [fault("EXC-0042", kind, NOW)]
    later = NOW + timedelta(minutes=10)
    full = avg(cfg, "EXC-0042", metric, later, faults) - avg(cfg, "EXC-0042", metric, later, [])
    assert full == pytest.approx(delta, rel=0.08)
    mid = NOW + timedelta(seconds=120)  # 2 of 5 minutes of ramp
    partial = avg(cfg, "EXC-0042", metric, mid, faults) - avg(cfg, "EXC-0042", metric, mid, [])
    assert 0.2 < partial / full < 0.75


def test_faulted_machine_keeps_operating_and_other_machines_unaffected(cfg: FleetConfig) -> None:
    faults = [fault("EXC-0042", "BEARING_WEAR", NOW)]
    for s in range(0, 3600, 300):
        assert simulate(cfg, cfg.machine("EXC-0042"), NOW + timedelta(seconds=s), faults)["status"] == "OPERATING"
    other = cfg.machine("TRK-0103")
    assert simulate(cfg, other, NOW, faults) == simulate(cfg, other, NOW, [])


def test_fuel_batch_fault_raises_fuel_and_marks_the_batch(cfg: FleetConfig) -> None:
    faults = [fault("TRK-0103", "FUEL_BATCH_ZONE", NOW)]
    later = NOW + timedelta(minutes=10)
    ratio = avg(cfg, "TRK-0103", "fuelRateLph", later, faults) / avg(cfg, "TRK-0103", "fuelRateLph", later, [])
    assert ratio == pytest.approx(1.35, rel=0.05)
    msg = simulate(cfg, cfg.machine("TRK-0103"), later, faults)
    assert msg["context"]["fuelBatchId"] == "FB-1120-X"  # Z3 batch, contaminated
    assert simulate(cfg, cfg.machine("TRK-0103"), later, [])["context"]["fuelBatchId"] == "FB-1120"


def test_sensor_failure_and_stop(cfg: FleetConfig) -> None:
    m = cfg.machine("LDR-0012")
    assert simulate(cfg, m, NOW, [fault("LDR-0012", "SENSOR_FAILURE", NOW)])["metrics"]["temperatureC"] is None
    stopped = simulate(cfg, m, NOW, [fault("LDR-0012", "STOPPED", NOW)])
    assert stopped["status"] == "STOPPED" and stopped["metrics"]["speedKmh"] == 0


def test_expired_fault_is_ignored(cfg: FleetConfig) -> None:
    f = {**fault("EXC-0042", "OVERHEATING", NOW - timedelta(hours=2)), "expiresAt": "2026-11-14T14:00:00Z"}
    m = cfg.machine("EXC-0042")
    assert simulate(cfg, m, NOW, [f]) == simulate(cfg, m, NOW, [])


def test_fault_types_match_the_injector_and_the_frontend() -> None:
    import re

    from fleet_faults import FAULT_TYPES
    from fleet_faults import ZONE_FAULTS as INJECTOR_ZONE_FAULTS

    assert set(FAULTS) == FAULT_TYPES and ZONE_FAULTS == INJECTOR_ZONE_FAULTS
    types_ts = (Path(__file__).resolve().parents[3] / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    frontend = set(re.findall(r'"([A-Z_]+)"', re.search(r"type FaultType = ([^;]+);", types_ts).group(1)))
    assert frontend <= set(FAULTS)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_store(faults: list) -> config.ParamStore:
    ssm = FakeSsm({config.FLEET_CONFIG: demo_fleet_raw(), config.SIM_FAULTS: {"faults": faults}})
    return config.ParamStore(client=ssm)


def test_run_publishes_whole_fleet_then_fast_loop_for_faulted_machine(load_handler) -> None:
    h = load_handler("simulator")
    iot = Recorder()
    clock = FakeClock()
    result = h.run(
        make_store([fault("EXC-0042", "BEARING_WEAR", NOW)]),
        h.Publisher("iot", {"iot-data": iot}),
        clock=clock,
        sleep=clock.sleep,
        budget_seconds=52,
        fast_interval=10,
    )
    topics = [c["topic"] for c in iot.of("publish")]
    expected = [f"arquifusion/MINA-NORTE/{m['machineId']}/telemetry" for m in demo_fleet_raw()["machines"]]
    assert topics[:MACHINES] == expected
    assert result == {"published": MACHINES + 5, "fastRounds": 5}
    assert set(topics[MACHINES:]) == {"arquifusion/MINA-NORTE/EXC-0042/telemetry"}


def test_run_without_faults_publishes_once(load_handler) -> None:
    h = load_handler("simulator")
    iot = Recorder()
    clock = FakeClock()
    result = h.run(make_store([]), h.Publisher("iot", {"iot-data": iot}), clock=clock, sleep=clock.sleep)
    assert result["fastRounds"] == 0 and len(iot.of("publish")) == MACHINES


def test_direct_mode_invokes_evaluator_and_firehose(load_handler, monkeypatch) -> None:
    h = load_handler("simulator")
    monkeypatch.setenv("AF_HEALTH_EVALUATOR_FUNCTION", "af-dev-health-evaluator")
    monkeypatch.setenv("AF_FIREHOSE_STREAM", "af-dev-telemetry")
    lam, fh = Recorder(), Recorder()
    clock = FakeClock()
    h.run(make_store([]), h.Publisher("direct", {"lambda": lam, "firehose": fh}), clock=clock, sleep=clock.sleep)
    assert len(lam.of("invoke")) == MACHINES and lam.of("invoke")[0]["InvocationType"] == "Event"
    assert len(fh.of("put_record_batch")[0]["Records"]) == MACHINES
