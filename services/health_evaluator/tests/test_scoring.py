import statistics

import pytest
from af_shared.contracts import Signal as ContractSignal
from scoring import (
    FleetBaseline,
    HealthConfig,
    MachineState,
    MetricBaseline,
    _confirm_severity,
    evaluate,
    severity_from_health,
)

PATTERN = [-2, -1, 0, 1, 2]


def normal(i: int) -> dict[str, float]:
    k = PATTERN[i % 5]
    return {
        "temperatureC": 78 + k,
        "vibrationMmS": 4.2 + 0.2 * k,
        "pressureBar": 210 + 2 * k,
        "fuelRateLph": 31 + 0.5 * k,
        "rpm": 1800 + 20 * k,
    }


def trained(n: int = 60) -> MachineState:
    state = MachineState()
    for i in range(n):
        state = evaluate(normal(i), state).state
    return state


def run(readings: list[dict[str, float]], state: MachineState, **kwargs):
    results = []
    for reading in readings:
        ev = evaluate(reading, state, **kwargs)
        results.append(ev)
        state = ev.state
    return results


# --- building blocks -------------------------------------------------------------------------


def test_ewma_starts_at_first_value_then_smooths() -> None:
    b = MetricBaseline().with_ewma(80, 0.2)
    assert b.ewma == 80
    assert b.with_ewma(90, 0.2).ewma == pytest.approx(0.2 * 90 + 0.8 * 80)


def test_welford_matches_statistics() -> None:
    values = [78.0, 80.5, 77.2, 79.9, 81.3, 76.4]
    b = MetricBaseline()
    for v in values:
        b = b.with_sample(v)
    assert b.n == len(values)
    assert b.mean == pytest.approx(statistics.mean(values))
    assert b.std == pytest.approx(statistics.stdev(values))


def test_baseline_roundtrip() -> None:
    b = MetricBaseline().with_sample(10).with_sample(12).with_ewma(11, 0.2)
    assert MetricBaseline.from_dict(b.to_dict()) == b


@pytest.mark.parametrize(
    ("health", "severity"),
    [(100, "OK"), (80, "OK"), (79.9, "WATCH"), (60, "WATCH"), (59.9, "WARNING"), (40, "WARNING"), (39.9, "CRITICAL")],
)
def test_severity_thresholds(health: float, severity: str) -> None:
    assert severity_from_health(health, HealthConfig()) == severity


def test_severity_rises_only_after_three_readings() -> None:
    cfg = HealthConfig()
    assert _confirm_severity("WARNING", MachineState(severity="OK", anomaly_streak=0), False, cfg) == ("OK", 1)
    assert _confirm_severity("WARNING", MachineState(severity="OK", anomaly_streak=1), False, cfg) == ("OK", 2)
    assert _confirm_severity("WARNING", MachineState(severity="OK", anomaly_streak=2), False, cfg) == ("WARNING", 0)


def test_hard_rule_and_recovery_are_immediate() -> None:
    cfg = HealthConfig()
    assert _confirm_severity("CRITICAL", MachineState(severity="OK"), True, cfg) == ("CRITICAL", 0)
    assert _confirm_severity("OK", MachineState(severity="WARNING", anomaly_streak=2), False, cfg) == ("OK", 0)


def test_config_from_ssm_params() -> None:
    cfg = HealthConfig.from_params({"temperatureC": "0.5"}, {"streak_required": "2", "watch_below": 85, "bogus": 1})
    assert cfg.weights == {"temperatureC": 0.5}
    assert cfg.streak_required == 2
    assert cfg.watch_below == 85.0


# --- machine behaviour -----------------------------------------------------------------------


def test_cold_start_without_baselines_is_ok() -> None:
    ev = evaluate(normal(0), MachineState())
    assert ev.health_score == 100
    assert ev.severity == "OK"
    assert ev.compared_against == []
    assert not ev.should_emit


def test_normal_operation_stays_ok() -> None:
    results = run([normal(i) for i in range(60, 80)], trained())
    assert all(r.severity == "OK" for r in results)
    assert min(r.health_score for r in results) >= 80


def test_bearing_wear_escalates_with_persistence_and_emits_once_per_level() -> None:
    state = trained()
    fault = [{**normal(i), "temperatureC": 92.4, "vibrationMmS": 7.8} for i in range(15)]
    results = run(fault, state)

    assert results[0].severity == "OK"  # one bad reading is not enough
    assert results[-1].severity == "CRITICAL"
    previous = "OK"
    for r in results:
        if r.should_emit:
            assert r.severity in {"WARNING", "CRITICAL"}
            assert r.severity != previous
        previous = r.severity
    assert sum(r.should_emit for r in results) <= 2
    top = results[-1].signals[0]
    assert top.metric in {"temperatureC", "vibrationMmS"}
    assert top.trend in {"RISING", "STABLE"}


def test_hard_rule_goes_straight_to_critical() -> None:
    state = trained()
    results = run([{**normal(i), "vibrationMmS": 30.0} for i in range(3)], state)
    first_critical = next(i for i, r in enumerate(results) if r.severity == "CRITICAL")
    assert first_critical <= 1
    assert results[first_critical].should_emit


def test_joint_behaviour_weighs_more_than_single_metric() -> None:
    state = trained()
    single = run([{**normal(0), "temperatureC": 86.0}] * 2, state)[-1]
    joint = run([{**normal(0), "temperatureC": 86.0, "vibrationMmS": 5.2}] * 2, state)[-1]
    assert joint.health_score < single.health_score


def test_fault_is_not_learned_as_normal() -> None:
    state = trained()
    n_before = state.baselines["temperatureC"].n
    results = run([{**normal(i), "temperatureC": 95.0} for i in range(10)], state)
    assert results[-1].state.baselines["temperatureC"].n == n_before
    assert results[-1].severity == "CRITICAL"  # single-metric overheating is not diluted away


def test_null_sensor_is_ignored() -> None:
    state = trained()
    ev = evaluate({**normal(0), "temperatureC": None}, state)
    assert ev.severity == "OK"
    assert ev.state.baselines["temperatureC"] == state.baselines["temperatureC"]


def test_low_pressure_is_dangerous_high_pressure_is_not() -> None:
    state = trained()
    high = run([{**normal(0), "pressureBar": 260.0}] * 5, state)[-1]
    low = run([{**normal(0), "pressureBar": 160.0}] * 5, state)[-1]
    assert high.severity == "OK"
    assert low.health_score < high.health_score


def test_fleet_baseline_detects_anomaly_on_new_machine() -> None:
    fleet = {"temperatureC": FleetBaseline(mean=78.0, std=2.0), "vibrationMmS": FleetBaseline(mean=4.2, std=0.5)}
    results = run(
        [{**normal(i), "temperatureC": 99.0, "vibrationMmS": 8.5} for i in range(8)], MachineState(), fleet=fleet
    )
    assert results[-1].compared_against == ["MODEL_FLEET"]
    assert results[-1].severity in {"WARNING", "CRITICAL"}


def test_signals_match_contract() -> None:
    state = trained()
    ev = run([{**normal(i), "temperatureC": 92.4, "vibrationMmS": 7.8} for i in range(4)], state)[-1]
    assert ev.signals
    for s in ev.signals:
        ContractSignal.model_validate(s.to_contract())
