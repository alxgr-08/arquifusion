"""Unit tests for retries and edge cases the end-to-end flow does not reach."""

import sys
from pathlib import Path

import incidents
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "statemachines" / "tests"))
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T14:07:00Z"
Z3 = {"lat": -23.6009, "lon": -69.9775}


def claimed(h: Harness, **kw) -> dict:
    return incidents.claim(
        h.table, {"event": anomaly_event(**kw)}, NOW, {"zoneWindowMinutes": 120, "zoneMinMachines": 3}
    )


def created(h: Harness, inc: dict, component: str = "BEARING") -> dict:
    diag = incidents.rule_diagnosis({"incident": inc, "context": {}})
    diag["diagnosis"]["component"] = component
    incidents.create_incident(h.table, {"incident": inc, "context": {}, "ai": {"response": diag}}, NOW)
    return {"incident": inc, "context": {"profile": Z3}, "created": {"diagnosis": diag}}


def test_claim_rejects_invalid_events_and_unknown_machines() -> None:
    h = Harness()
    with pytest.raises(incidents.TaskError):
        incidents.claim(h.table, {"event": {"source": "x"}}, NOW, {})
    with pytest.raises(incidents.TaskError):
        claimed(h, severity="WATCH")
    with pytest.raises(incidents.TaskError):
        claimed(h, machine_id="XXX-0001")


def test_reserve_parts_is_idempotent_on_retry() -> None:
    h = Harness()
    payload = created(h, claimed(h))
    fleet = incidents._fleet(h.store)
    first = incidents.reserve_parts(h.table, payload, fleet, NOW)
    second = incidents.reserve_parts(h.table, payload, fleet, NOW)
    assert first["reserved"] == second["reserved"] == [{"partId": "BRG-SPH-22", "warehouse": "WH-NORTE", "qty": 1}]
    stock = h.table.get("PART#BRG-SPH-22", "STOCK#WH-NORTE")
    assert (stock["available"], stock["reserved"]) == (1, 1)


def test_two_incidents_cannot_take_the_last_part() -> None:
    h = Harness()
    fleet = incidents._fleet(h.store)
    results = []
    for machine in ("EXC-0042", "EXC-0051", "TRK-0103", "TRK-0107"):
        payload = created(h, claimed(h, machine_id=machine))
        results.append(incidents.reserve_parts(h.table, payload, fleet, NOW))
    assert [r["reserved"][0]["warehouse"] if r["reserved"] else None for r in results] == [
        "WH-NORTE",
        "WH-NORTE",
        "WH-SUR",
        None,
    ]
    assert results[-1]["hasMissing"]


@pytest.mark.parametrize(
    ("metrics", "component"),
    [
        ({"temperatureC": 3, "vibrationMmS": 2.6}, "BEARING"),
        ({"pressureBar": 4}, "HYDRAULIC_SYSTEM"),
        ({"fuelRateLph": 3}, "FUEL_SYSTEM"),
        ({"temperatureC": 3}, "COOLING_SYSTEM"),
        ({"speedKmh": 2.5}, "ENGINE"),
    ],
)
def test_rule_fallback_matches_the_contract(metrics: dict, component: str) -> None:
    from af_shared import DiagnosisResult

    h = Harness()
    inc = claimed(h)
    inc["anomaly"]["signals"] = [
        {"metric": m, "value": 1, "baseline": 0, "zScore": z, "trend": "RISING"} for m, z in metrics.items()
    ]
    out = incidents.rule_diagnosis({"incident": inc, "context": {}})
    DiagnosisResult.model_validate(out)
    assert out["diagnosis"]["component"] == component


def test_assignment_retry_keeps_the_same_technician() -> None:
    import assignment

    h = Harness()
    payload = created(h, claimed(h))
    req = {"incident": payload["incident"], "diagnosis": payload["created"]["diagnosis"], "profile": Z3}
    first = assignment.assign(h.table, req, NOW)
    retry = assignment.assign(h.table, req, NOW)
    assert first["techId"] == retry["techId"] == "T-07" and retry.get("retried")
    tech = h.table.get("TECH#T-07", "PROFILE")
    assert tech["queue"] == 1 and tech["availability"] == "BUSY"


def test_route_matrix_is_used_when_available() -> None:
    import assignment
    from af_shared.fakes import Recorder

    techs = [{"techId": "T-01", "lat": -23.6, "lon": -70.4}, {"techId": "T-02", "lat": -23.7, "lon": -70.4}]
    loc = Recorder({"calculate_route_matrix": {"RouteMatrix": [[{"DurationSeconds": 600}], [{"DurationSeconds": 60}]]}})
    etas, method = assignment.eta_minutes(techs, (-23.65, -70.39), 25, loc, "af-dev-routes")
    assert (etas, method) == ([10.0, 1.0], "AMAZON_LOCATION")
    assert loc.of("calculate_route_matrix")[0]["DeparturePositions"][0] == [-70.4, -23.6]  # [lon, lat]
    assert assignment.eta_minutes(techs, (-23.65, -70.39), 25)[1] == "HAVERSINE"
