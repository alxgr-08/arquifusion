import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from af_shared import Telemetry, config
from af_shared.fakes import FakeSsm, Recorder, demo_fleet_raw
from af_shared.fleet import SIMULATOR_KEYS, FleetConfig, subset
from fleet_sim import simulate
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[3] / "contracts/telemetry.schema.json").read_text())
NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)


@pytest.fixture
def cfg() -> FleetConfig:
    return FleetConfig.model_validate(subset(demo_fleet_raw(), SIMULATOR_KEYS))


def fault(machine: str, kind: str, started: datetime, intensity: str = "WARNING") -> dict:
    return {
        "faultId": "F-1",
        "machineId": machine,
        "faultType": kind,
        "intensity": intensity,
        "startedAt": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def test_every_message_matches_the_contract(cfg: FleetConfig) -> None:
    validator = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for minute in range(0, 180, 7):
        for machine in cfg.machines:
            msg = simulate(cfg, machine, NOW + timedelta(minutes=minute), [])
            assert not list(validator.iter_errors(msg)), msg
            Telemetry.model_validate(msg)


def test_same_inputs_same_message_so_retries_deduplicate(cfg: FleetConfig) -> None:
    m = cfg.machines[0]
    assert simulate(cfg, m, NOW, []) == simulate(cfg, m, NOW, [])
    assert simulate(cfg, m, NOW, [])["messageId"] != simulate(cfg, m, NOW + timedelta(seconds=10), [])["messageId"]


def test_bearing_fault_ramps_temperature_and_vibration(cfg: FleetConfig) -> None:
    m = cfg.machine("EXC-0042")
    faults = [fault("EXC-0042", "BEARING_WEAR", NOW)]
    model = cfg.model_of(m.model)

    def avg(metric: str, at: datetime, fs: list) -> float:
        return sum(simulate(cfg, m, at + timedelta(seconds=s), fs)["metrics"][metric] for s in range(40)) / 40

    healthy = avg("temperatureC", NOW + timedelta(minutes=5), [])
    degraded = avg("temperatureC", NOW + timedelta(minutes=5), faults)
    assert degraded - healthy == pytest.approx(3.5 * model.metrics["temperatureC"].std, rel=0.05)
    assert avg("vibrationMmS", NOW + timedelta(minutes=5), faults) > avg("vibrationMmS", NOW + timedelta(minutes=5), [])
    # half-way through the 60 s ramp the drift is about half
    mid = NOW + timedelta(seconds=30)
    half = avg("temperatureC", mid, faults) - avg("temperatureC", mid, [])
    assert 0.2 < half / (degraded - healthy) < 0.9


def test_faulted_machine_keeps_operating_and_other_machines_unaffected(cfg: FleetConfig) -> None:
    faults = [fault("EXC-0042", "BEARING_WEAR", NOW)]
    for s in range(0, 3600, 300):
        assert simulate(cfg, cfg.machine("EXC-0042"), NOW + timedelta(seconds=s), faults)["status"] == "OPERATING"
    other = cfg.machine("HTK-0103")
    assert simulate(cfg, other, NOW, faults) == simulate(cfg, other, NOW, [])


def test_sensor_failure_sends_null_and_stop_fault_zeroes_motion(cfg: FleetConfig) -> None:
    m = cfg.machine("LDR-0202")
    assert simulate(cfg, m, NOW, [fault("LDR-0202", "SENSOR_FAILURE", NOW)])["metrics"]["temperatureC"] is None
    stopped = simulate(cfg, m, NOW, [fault("LDR-0202", "STOPPED", NOW)])
    assert stopped["status"] == "STOPPED" and stopped["metrics"]["speedKmh"] == 0


def test_fuel_contamination_changes_batch_for_zone_correlation(cfg: FleetConfig) -> None:
    msg = simulate(cfg, cfg.machine("HTK-0103"), NOW, [fault("HTK-0103", "FUEL_CONTAMINATION", NOW)])
    assert msg["context"]["fuelBatchId"] == "FB-1120X"


def test_expired_fault_is_ignored(cfg: FleetConfig) -> None:
    f = {**fault("EXC-0042", "OVERHEAT", NOW - timedelta(hours=2)), "expiresAt": "2026-11-14T14:00:00Z"}
    m = cfg.machine("EXC-0042")
    assert simulate(cfg, m, NOW, [f]) == simulate(cfg, m, NOW, [])


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_store(faults: list) -> config.ParamStore:
    ssm = FakeSsm(
        {config.FLEET_CONFIG: subset(demo_fleet_raw(), SIMULATOR_KEYS), config.SIM_FAULTS: {"faults": faults}}
    )
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
    )
    topics = [c["topic"] for c in iot.of("publish")]
    assert topics[:13] == [f"arquifusion/MINA-NORTE/{m['machineId']}/telemetry" for m in demo_fleet_raw()["machines"]]
    assert result == {"published": 13 + 5, "fastRounds": 5}
    assert set(topics[13:]) == {"arquifusion/MINA-NORTE/EXC-0042/telemetry"}


def test_run_without_faults_publishes_once(load_handler) -> None:
    h = load_handler("simulator")
    iot = Recorder()
    clock = FakeClock()
    result = h.run(make_store([]), h.Publisher("iot", {"iot-data": iot}), clock=clock, sleep=clock.sleep)
    assert result["fastRounds"] == 0
    assert len(iot.of("publish")) == 13


def test_direct_mode_invokes_evaluator_and_firehose(load_handler, monkeypatch) -> None:
    h = load_handler("simulator")
    monkeypatch.setenv("AF_HEALTH_EVALUATOR_FUNCTION", "af-dev-health-evaluator")
    monkeypatch.setenv("AF_FIREHOSE_STREAM", "af-dev-telemetry")
    lam, fh = Recorder(), Recorder()
    clock = FakeClock()
    h.run(make_store([]), h.Publisher("direct", {"lambda": lam, "firehose": fh}), clock=clock, sleep=clock.sleep)
    assert len(lam.of("invoke")) == 13 and lam.of("invoke")[0]["InvocationType"] == "Event"
    assert len(fh.of("put_record_batch")[0]["Records"]) == 13
