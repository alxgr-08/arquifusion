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


def test_claim_retry_after_lost_response_keeps_the_same_incident() -> None:
    h = Harness()
    settings = {"zoneWindowMinutes": 120, "zoneMinMachines": 3}
    payload = {"event": anomaly_event(), "executionId": "arn:aws:states:::execution:af-dev-incident:run-1"}
    first = incidents.claim(h.table, payload, NOW, settings)
    retry = incidents.claim(h.table, payload, "2026-11-13T14:07:09Z", settings)  # response of `first` was lost
    assert retry["duplicate"] is False
    assert (retry["incidentId"], retry["incidentSK"], retry["createdAt"]) == (
        first["incidentId"],
        first["incidentSK"],
        first["createdAt"],
    )
    other = incidents.claim(h.table, {**payload, "executionId": "arn:...:run-2"}, NOW, settings)
    assert other["duplicate"] is True and other["incidentId"] == first["incidentId"]
    incidents.release_claim_only(h.table, first)
    profile = h.table.get_item(Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"})["Item"]
    assert "openIncidentId" not in profile and "openIncidentExecution" not in profile


def test_replayed_alert_after_its_incident_closed_is_ignored() -> None:
    h = Harness()
    settings = {"zoneWindowMinutes": 120, "zoneMinMachines": 3}
    first = claimed(h)
    incidents.release_claim_only(h.table, first)  # incident handled and machine freed
    replay = incidents.claim(h.table, {"event": anomaly_event(), "executionId": "arn:...:replay"}, NOW, settings)
    assert replay["duplicate"] is True and replay.get("replayed") is True
    later = incidents.claim(
        h.table,
        {"event": anomaly_event(detected_at="2026-11-13T18:00:00Z"), "executionId": "arn:...:new"},
        NOW,
        settings,
    )
    assert later["duplicate"] is False and later["incidentId"] != first["incidentId"]


def test_reserve_parts_can_stay_in_the_zone_warehouse() -> None:
    h = Harness()
    inc = claimed(h)
    inc["settings"] = {"partsFromOtherWarehouses": False}
    payload = created(h, inc)
    fleet = incidents._fleet(h.store)
    for wh in fleet.warehouses_for_zone(inc["zoneId"])[:1]:
        h.table.update_item(
            Key={"PK": "PART#BRG-SPH-22", "SK": f"STOCK#{wh}"},
            UpdateExpression="SET available = :z",
            ExpressionAttributeValues={":z": 0},
        )
    before = {k: dict(v) for k, v in h.table.items.items() if k[0].startswith("PART#")}
    out = incidents.reserve_parts(h.table, payload, fleet, NOW)
    assert out["reserved"] == [] and out["hasMissing"] is True
    after = {k: dict(v) for k, v in h.table.items.items() if k[0].startswith("PART#")}
    assert after == before  # no other warehouse was touched


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


def test_latest_reading_compares_legacy_and_fractional_timestamps() -> None:
    h = Harness()
    pk = "MACHINE#ENS-4999"
    for at, batch in [("12:00:00Z", "OLD"), ("12:00:00.100000Z", "NEW"), ("11:59:59.999999Z", "OLDER")]:
        h.table.put_item(Item={"PK": pk, "SK": "READING#2026-11-15T" + at, "context": {"fuelBatchId": batch}})
    assert incidents._latest_reading(h.table, "ENS-4999")["context"]["fuelBatchId"] == "NEW"
    h.table.put_item(Item={"PK": pk, "SK": "READING#2026-11-15T12:00:01Z", "context": {"fuelBatchId": "NEXT"}})
    assert incidents._latest_reading(h.table, "ENS-4999")["context"]["fuelBatchId"] == "NEXT"


def test_claim_cannot_take_a_machine_owned_by_another_operation() -> None:
    h = Harness()
    event = anomaly_event()
    key = {"PK": f"MACHINE#{event['detail']['machineId']}", "SK": "PROFILE"}
    h.table.update_item(Key=key, UpdateExpression="SET operationId = :op", ExpressionAttributeValues={":op": "OTHER"})
    before = dict(h.table.get_item(Key=key)["Item"])
    with pytest.raises(incidents.TaskError, match="operation does not own"):
        incidents.claim(h.table, {"event": event, "executionId": "foreign"}, NOW, {})
    assert h.table.get_item(Key=key)["Item"] == before


def test_late_escalation_cannot_recreate_a_released_or_deleted_profile() -> None:
    h = Harness()
    inc = claimed(h)
    created(h, inc)
    incidents.release_claim_only(h.table, inc)
    key = {"PK": f"MACHINE#{inc['machineId']}", "SK": "PROFILE"}
    incidents._escalate(h.table, inc["machineId"], inc["operationId"], inc["incidentSK"], "CRITICAL", 20, NOW)
    assert "openIncidentSeverity" not in h.table.get_item(Key=key)["Item"]
    h.table.delete_item(Key=key)
    incidents._escalate(h.table, inc["machineId"], inc["operationId"], inc["incidentSK"], "CRITICAL", 20, NOW)
    assert h.table.get_item(Key=key).get("Item") is None


def test_repeated_escalation_appends_its_history_once() -> None:
    h = Harness()
    inc = claimed(h)
    created(h, inc)
    for _ in range(2):
        incidents._escalate(h.table, inc["machineId"], inc["operationId"], inc["incidentSK"], "CRITICAL", 20, NOW)
    row = h.table.get_item(Key={"PK": f"MACHINE#{inc['machineId']}", "SK": inc["incidentSK"]})["Item"]
    assert row["severity"] == "CRITICAL"
    assert sum(event["event"] == "ESCALATED" for event in row["timeline"]) == 1


def test_maintenance_reset_replay_uses_the_original_recorded_hours_and_date() -> None:
    h = Harness()
    inc = claimed(h)
    created(h, inc)
    h.table.update_item(
        Key={"PK": f"MACHINE#{inc['machineId']}", "SK": inc["incidentSK"]},
        UpdateExpression="SET #st = :closed, closedAt = :at, closing = :closing",
        ExpressionAttributeNames={"#st": "state"},
        ExpressionAttributeValues={":closed": "RESOLVED", ":at": NOW, ":closing": {"resolvedAt": NOW}},
    )
    h.table.put_item(
        Item={
            "PK": f"MACHINE#{inc['machineId']}",
            "SK": f"MAINT#{NOW[:10]}#MNT-{inc['incidentId']}",
            "incidentId": inc["incidentId"],
            "engineHours": 100,
        }
    )
    key = {"PK": f"MACHINE#{inc['machineId']}", "SK": "PROFILE"}
    incidents.update_machine_state(h.table, {"incident": inc}, NOW)
    h.table.update_item(Key=key, UpdateExpression="SET engineHours = :hours", ExpressionAttributeValues={":hours": 140})
    retry = incidents.update_machine_state(h.table, {"incident": inc}, "2026-11-14T14:07:00Z")
    assert retry["lastMaintenanceHours"] == 100
    assert h.table.get_item(Key=key)["Item"]["lastMaintenanceAt"] == NOW
    h.table.update_item(Key=key, UpdateExpression="SET operationId = :op", ExpressionAttributeValues={":op": "OTHER"})
    before = dict(h.table.get_item(Key=key)["Item"])
    incidents.update_machine_state(h.table, {"incident": inc}, "2026-11-15T14:07:00Z")
    assert h.table.get_item(Key=key)["Item"] == before
