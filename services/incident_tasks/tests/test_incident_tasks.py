"""Unit tests for retries and edge cases the end-to-end flow does not reach."""

import sys
from pathlib import Path

import incidents
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "statemachines" / "tests"))
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T14:07:00Z"


def claimed(h: Harness, **kw) -> dict:
    return incidents.claim(h.table, {"event": anomaly_event(**kw)}, NOW, {"zoneWindowMinutes": 120})


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
    inc = claimed(h)
    payload = {
        "incident": inc,
        "context": {"profile": {"lat": -23.6509, "lon": -70.3975}},
        "created": {"diagnosis": {"recommendedParts": [{"partId": "BRG-KIT-02", "qty": 1}]}},
    }
    incidents.create_incident(h.table, {**payload, "ai": {"response": None}}, NOW)
    first = incidents.reserve_parts(h.table, payload, h.store.get_json("/af/incident/catalog"), NOW)
    second = incidents.reserve_parts(h.table, payload, h.store.get_json("/af/incident/catalog"), NOW)
    assert first["reserved"] == second["reserved"] == [{"partId": "BRG-KIT-02", "warehouseId": "WH-SUR", "qty": 1}]
    stock = h.table.get("PART#BRG-KIT-02", "STOCK#WH-SUR")
    assert (stock["available"], stock["reserved"]) == (0, 1)


def test_two_incidents_cannot_take_the_last_part() -> None:
    h = Harness()
    catalog = h.store.get_json("/af/incident/catalog")
    results = []
    for machine in ("EXC-0042", "HTK-0103", "HTK-0104", "LDR-0202"):
        inc = claimed(h, machine_id=machine)
        payload = {
            "incident": inc,
            "context": {"profile": {"lat": -23.6509, "lon": -70.3975}},
            "created": {"diagnosis": {"recommendedParts": [{"partId": "BRG-KIT-02", "qty": 1}]}},
        }
        incidents.create_incident(h.table, {**payload, "ai": {"response": None}}, NOW)
        results.append(incidents.reserve_parts(h.table, payload, catalog, NOW))
    assert [r["reserved"][0]["warehouseId"] if r["reserved"] else None for r in results] == [
        "WH-SUR",
        "WH-NORTE",
        "WH-NORTE",
        None,
    ]
    assert results[-1]["hasMissing"]


def test_rule_diagnosis_picks_most_specific_mode() -> None:
    h = Harness()
    inc = claimed(h)
    catalog = h.store.get_json("/af/incident/catalog")
    ctx = {"profile": {"candidateFailureModes": catalog["failureModes"]}, "zone": {"machines": []}}
    out = incidents.rule_diagnosis({"incident": inc, "context": ctx})
    assert out["failureMode"] == "BEARING_WEAR" and out["recommendedParts"] == [{"partId": "BRG-KIT-02", "qty": 1}]
    assert set(out["messages"]) == {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
    inc["anomaly"]["signals"] = [
        {"metric": "pressureBar", "value": 150, "baseline": 215, "zScore": -4.0, "trend": "FALLING"}
    ]
    assert incidents.rule_diagnosis({"incident": inc, "context": ctx})["failureMode"] == "HYDRAULIC_LEAK"
    inc["anomaly"]["signals"] = [{"metric": "speedKmh", "value": 1, "baseline": 5, "zScore": -2.5, "trend": "FALLING"}]
    unknown = incidents.rule_diagnosis({"incident": inc, "context": ctx})
    assert unknown["failureMode"] == "UNKNOWN" and unknown["recommendedParts"] == []


def test_assignment_retry_keeps_the_same_technician() -> None:
    import assignment

    h = Harness()
    inc = claimed(h)
    incidents.create_incident(h.table, {"incident": inc, "context": {}, "ai": {"response": None}}, NOW)
    payload = {
        "incident": inc,
        "diagnosis": {"requiredSpecialty": "MECHANICAL"},
        "profile": {"lat": -23.6509, "lon": -70.3975},
    }
    first = assignment.assign(h.table, payload, NOW)
    retry = assignment.assign(h.table, payload, NOW)
    assert first["techId"] == retry["techId"] == "T-04" and retry.get("retried")
    assert h.table.get("TECH#T-04", "PROFILE")["queueLength"] == 1


def test_route_matrix_is_used_when_available() -> None:
    import assignment
    from af_shared.fakes import Recorder

    techs = [{"techId": "T-01", "lat": -23.6, "lon": -70.4}, {"techId": "T-02", "lat": -23.7, "lon": -70.4}]
    loc = Recorder({"calculate_route_matrix": {"RouteMatrix": [[{"DurationSeconds": 600}], [{"DurationSeconds": 60}]]}})
    etas, method = assignment.eta_minutes(techs, (-23.65, -70.39), 25, loc, "af-dev-routes")
    assert (etas, method) == ([10.0, 1.0], "AMAZON_LOCATION")
    assert loc.of("calculate_route_matrix")[0]["DeparturePositions"][0] == [-70.4, -23.6]  # [lon, lat]
    assert assignment.eta_minutes(techs, (-23.65, -70.39), 25)[1] == "HAVERSINE"
