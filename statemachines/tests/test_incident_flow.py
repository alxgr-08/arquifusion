"""End-to-end runs of statemachines/incident.asl.json with the real Rol 2 Lambdas and a fake DynamoDB."""

import pytest
from asl_runner import Paused, check_definition
from flow_harness import Harness, ack_all, anomaly_event, definition


@pytest.mark.parametrize("name", ["incident", "preventive"])
def test_definitions_are_well_formed(name: str) -> None:
    assert check_definition(definition(name)) == []


def test_happy_path_detect_assign_notify_resolve() -> None:
    h = Harness()
    runner, out = h.run_incident(anomaly_event(), ack_all(harness=h))
    assert runner.visited[-1] == "Done"
    for state in (
        "EnrichContext",
        "AIDiagnosis",
        "CreateIncident",
        "ReserveParts",
        "AssignTechnician",
        "NotifyByRole",
        "WaitForTechnician",
        "WaitForResolution",
        "CloseIncident",
    ):
        assert state in runner.visited
    assert "RuleDiagnosis" not in runner.visited

    inc = h.incident("EXC-0042")
    assert inc["state"] == "CLOSED" and inc["GSI1PK"] == "OP#MINA-NORTE#INCIDENT#CLOSED"
    assert inc["failureMode"] == "BEARING_WEAR" and inc["aiSource"] == "BEDROCK"
    assert inc["partsReserved"] == [{"partId": "BRG-KIT-02", "warehouseId": "WH-SUR", "qty": 1}]  # nearest to Z3
    assert inc["assignedTech"] == "T-04"  # MECHANICAL and closest
    assert 110 <= inc["downtimeMinutes"] <= 125  # detected -> resolved (arrival 20 min + repair 95 min)
    events = [e["event"] for e in inc["timeline"]]
    assert events[:4] == ["DETECTED", "DIAGNOSED", "PARTS_RESERVED", "ASSIGNED"]
    assert "NOTIFIED" in events and "CLOSED" in events

    stock = h.table.get("PART#BRG-KIT-02", "STOCK#WH-SUR")
    assert (stock["qty"], stock["available"], stock["reserved"], stock["reservations"]) == (0, 0, 0, {})
    tech = h.table.get("TECH#T-04", "PROFILE")
    assert tech["availability"] == "AVAILABLE" and tech["GSI1SK"] == "AVAILABLE#T-04"
    profile = h.table.get("MACHINE#EXC-0042", "PROFILE")
    assert "openIncidentId" not in profile and profile["lastMaintenanceHours"] == 12450.5
    maint = [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#EXC-0042" and sk.startswith("MAINT#")]
    assert maint[0]["type"] == "CORRECTIVE" and maint[0]["partsUsed"][0]["partId"] == "BRG-KIT-02"

    # WARNING matrix: technician by email, maintenance lead immediately; operator/supervisor only on the panel
    assert ("INCIDENT", "TECHNICIAN", "EMAIL") in h.published()
    assert ("INCIDENT", "MAINT_LEAD", "IMMEDIATE") in h.published()
    assert not any(r in ("OPERATOR", "SUPERVISOR", "MANAGER") for k, r, _ in h.published() if k == "INCIDENT")
    assert ("RESOLVED", "SUPERVISOR", "IMMEDIATE") in h.published()
    assert out["closed"]["downtimeMinutes"] == inc["downtimeMinutes"]


def test_ai_request_matches_contract() -> None:
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    h = Harness()
    h.run_incident(anomaly_event(), ack_all(harness=h))
    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/ai-diagnosis.request.schema.json").read_text()
    )
    assert not list(Draft202012Validator(schema).iter_errors(h.ai_calls[0]))


def test_critical_notifies_operator_by_sms_and_supervisor() -> None:
    h = Harness()
    h.run_incident(anomaly_event(severity="CRITICAL"), ack_all(harness=h))
    sent = h.published()
    assert ("INCIDENT", "OPERATOR", "SMS") in sent and ("INCIDENT", "SUPERVISOR", "IMMEDIATE") in sent
    assert ("INCIDENT", "TECHNICIAN", "SMS") in sent


@pytest.mark.parametrize("ai", [RuntimeError("Bedrock throttled"), {"source": "BEDROCK", "garbage": True}])
def test_ai_failure_or_invalid_answer_falls_back_to_rules(ai) -> None:
    def broken(_payload):
        if isinstance(ai, Exception):
            raise ai
        return ai

    h = Harness(ai=broken)
    runner, _ = h.run_incident(anomaly_event(), ack_all(harness=h))
    inc = h.incident("EXC-0042")
    assert inc["aiSource"] == "RULES" and inc["failureMode"] == "BEARING_WEAR"
    assert runner.visited[-1] == "Done"
    assert ("RuleDiagnosis" in runner.visited) == isinstance(ai, Exception)


def test_duplicate_events_do_not_open_a_second_incident_and_escalation_is_notified() -> None:
    h = Harness()

    def hold(_info):
        raise Paused()

    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)  # first execution is waiting for the technician
    runner, _ = h.run_incident(anomaly_event(), hold)
    assert runner.visited[-1] == "DuplicateIgnored"
    runner, _ = h.run_incident(anomaly_event(severity="CRITICAL"), hold)
    assert runner.visited[-2:] == ["NotifyEscalation", "DuplicateIgnored"]
    inc = h.incident("EXC-0042")
    assert inc["severity"] == "CRITICAL" and inc["GSI1SK"].startswith("1#")
    assert ("ESCALATED", "OPERATOR", "SMS") in h.published()
    assert len([k for k in h.table.items if k[0] == "MACHINE#EXC-0042" and k[1].startswith("INCIDENT#")]) == 1


def test_technician_timeout_reassigns_to_another_technician() -> None:
    h = Harness()
    calls = {"n": 0}

    def first_times_out(info):
        calls["n"] += 1
        if calls["n"] == 1:
            return "TIMEOUT"
        return ack_all(harness=h)(info)

    runner, _ = h.run_incident(anomaly_event(), first_times_out)
    assert runner.visited.count("AssignTechnician") == 2 and "ReleaseTechnician" in runner.visited
    inc = h.incident("EXC-0042")
    assert inc["assignedTech"] != "T-04"
    assert [e["event"] for e in inc["timeline"]].count("ACK_TIMEOUT") == 1
    assert ("TECH_TIMEOUT", "MAINT_LEAD", "IMMEDIATE") in h.published()
    assert h.table.get("TECH#T-04", "PROFILE")["availability"] == "AVAILABLE"


def test_no_technician_available_waits_and_retries() -> None:
    h = Harness()
    for (pk, _sk), item in list(h.table.items.items()):
        if pk.startswith("TECH#"):
            item["availability"] = "OFF_SHIFT"

    def hold(_info):
        raise Paused()

    runner = None
    with pytest.raises(RuntimeError):  # loops forever waiting for someone to come on shift
        from asl_runner import Runner

        runner = Runner(definition("incident"), h.functions(), hold)
        runner.run(anomaly_event())
    assert runner.visited.count("WaitBeforeRetry") > 5 and "NotifyNoTechnician" in runner.visited


def test_zone_incident_when_three_machines_fail_in_the_same_zone() -> None:
    h = Harness(ai=lambda p: None)  # rules only: deterministic failure modes
    fuel = [
        {"metric": "fuelRateLph", "value": 130.0, "baseline": 95.0, "zScore": 3.4, "trend": "RISING"},
        {"metric": "rpm", "value": 1700.0, "baseline": 1900.0, "zScore": -2.4, "trend": "FALLING"},
    ]

    def hold(_info):
        raise Paused()

    for mid, model in (("HTK-0103", "HTK-797"), ("HTK-0104", "HTK-797")):
        with pytest.raises(Paused):
            h.run_incident(anomaly_event(mid, model=model, signals=fuel), hold)
    with pytest.raises(Paused):
        runner, _ = h.run_incident(anomaly_event("LDR-0202", model="LDR-980", signals=fuel), hold)

    zone_rows = [i for (pk, sk), i in h.table.items.items() if sk.startswith("ZINC#")]
    assert len(zone_rows) == 1
    zinc = zone_rows[0]
    assert zinc["machineIds"] == {"HTK-0103", "HTK-0104", "LDR-0202"} and zinc["GSI1SK"].startswith("1#")
    assert "lote de combustible FB-1120" in zinc["hypothesis"]
    assert ("ZONE_INCIDENT", "SUPERVISOR", "IMMEDIATE") in h.published()
    assert ("ZONE_INCIDENT", "MANAGER", "IMMEDIATE") in h.published()
    assert all(h.incident(m).get("zoneIncidentId") == zinc["zoneIncidentId"] for m in ("HTK-0103", "LDR-0202"))


def test_recurrence_and_parts_shortage() -> None:
    h = Harness()
    h.run_incident(anomaly_event(), ack_all(harness=h))
    h.clock.advance(days=3)
    h.run_incident(anomaly_event(detected_at="2026-11-16T14:00:00Z"), ack_all(harness=h))
    first, second = sorted(
        [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#EXC-0042" and sk.startswith("INCIDENT#")],
        key=lambda r: r["SK"],
    )
    assert second["recurrenceOf"] == first["incidentId"]
    assert second["partsReserved"][0]["warehouseId"] == "WH-NORTE"  # WH-SUR ran out after the first repair

    h.clock.advance(days=1)
    h.run_incident(anomaly_event(detected_at="2026-11-17T14:00:00Z"), ack_all(harness=h))
    h.clock.advance(days=1)
    runner, _ = h.run_incident(anomaly_event(detected_at="2026-11-18T14:00:00Z"), ack_all(harness=h))
    assert "NotifyRestock" in runner.visited
    assert ("RESTOCK", "MAINT_LEAD", "IMMEDIATE") in h.published()
