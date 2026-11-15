"""End-to-end runs of statemachines/incident.asl.json with the real Rol 2 Lambdas, the real ai-diagnosis rules of
Rol 3 and a fake DynamoDB, using the demo fleet of contracts/examples/fleet-config.json."""

import pytest
from asl_runner import Paused, Runner, check_definition
from flow_harness import Harness, ack_all, anomaly_event, definition

FUEL = [
    {"metric": "fuelRateLph", "value": 130.0, "baseline": 95.0, "zScore": 3.4, "trend": "RISING"},
    {"metric": "temperatureC", "value": 89.0, "baseline": 85.0, "zScore": 1.6, "trend": "RISING"},
]


def hold(_info):
    raise Paused()


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
    assert inc["state"] == "RESOLVED" and inc["GSI1PK"] == "OP#MINA-NORTE#INCIDENT#CLOSED"
    assert inc["aiDiagnosis"]["component"] == "BEARING" and inc["aiDiagnosis"]["source"] == "RULES"
    assert set(inc["messages"]) == {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
    assert inc["partsReserved"] == [{"partId": "BRG-SPH-22", "warehouse": "WH-NORTE", "qty": 1}]  # WH-NORTE serves Z3
    assert inc["assignedTech"] == "T-07"  # BEARING specialist and closest to Z3
    assert 110 <= inc["downtimeMinutes"] <= 125
    # ARRIVED/RESOLVED are written once by the workflow (CertifyArrival, CloseIncident), not by the API
    assert [e["event"] for e in inc["timeline"]] == ["DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED"]
    assert {e["event"] for e in inc["activity"]} >= {"DIAGNOSED", "PARTS_RESERVED", "NOTIFIED", "CLOSED"}

    stock = h.table.get("PART#BRG-SPH-22", "STOCK#WH-NORTE")
    assert (stock["qty"], stock["available"], stock["reserved"], stock["reservations"]) == (1, 1, 0, {})
    tech = h.table.get("TECH#T-07", "PROFILE")
    assert tech["availability"] == "AVAILABLE" and tech["queue"] == 0 and tech["GSI1SK"] == "AVAILABLE#T-07"
    profile = h.table.get("MACHINE#EXC-0042", "PROFILE")
    assert "openIncidentId" not in profile and profile["lastMaintenanceHours"] == 12450
    maint = [i for (pk, sk), i in h.table.items.items() if pk == "MACHINE#EXC-0042" and sk.startswith("MAINT#")]
    assert maint[0]["type"] == "CORRECTIVE" and maint[0]["partsReplaced"] == ["BRG-SPH-22"]

    assert ("INCIDENT", "TECHNICIAN", "EMAIL") in h.published()
    assert ("INCIDENT", "MAINT_LEAD", "IMMEDIATE") in h.published()
    assert not any(r in ("OPERATOR", "SUPERVISOR", "MANAGER") for k, r, _ in h.published() if k == "INCIDENT")
    assert ("RESOLVED", "SUPERVISOR", "IMMEDIATE") in h.published()
    assert out["closed"]["downtimeMinutes"] == inc["downtimeMinutes"]


def test_ai_request_matches_the_contract() -> None:
    import sys
    from pathlib import Path

    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    from validate_contracts import ROOT, load_registry

    h = Harness()
    h.run_incident(anomaly_event(), ack_all(harness=h))
    import json

    schema = json.loads((ROOT / "ai-diagnosis-request.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=load_registry())
    assert not list(validator.iter_errors(h.ai_calls[0]))


def test_critical_notifies_operator_by_sms_and_supervisor() -> None:
    h = Harness()
    h.run_incident(anomaly_event(severity="CRITICAL"), ack_all(harness=h))
    sent = h.published()
    assert ("INCIDENT", "OPERATOR", "SMS") in sent and ("INCIDENT", "SUPERVISOR", "IMMEDIATE") in sent
    assert ("INCIDENT", "TECHNICIAN", "SMS") in sent
    assert h.incident("EXC-0042")["aiDiagnosis"]["stopMachine"] is True


@pytest.mark.parametrize("ai", [RuntimeError("Lambda timeout"), {"source": "BEDROCK", "garbage": True}])
def test_ai_failure_or_invalid_answer_falls_back_to_rules(ai) -> None:
    def broken(_payload):
        if isinstance(ai, Exception):
            raise ai
        return ai

    h = Harness(ai=broken)
    runner, _ = h.run_incident(anomaly_event(), ack_all(harness=h))
    inc = h.incident("EXC-0042")
    assert inc["aiDiagnosis"]["source"] == "RULES" and inc["aiDiagnosis"]["component"] == "BEARING"
    assert runner.visited[-1] == "Done"
    assert ("RuleDiagnosis" in runner.visited) == isinstance(ai, Exception)


def test_bedrock_answer_is_stored_as_the_web_expects() -> None:
    from flow_harness import ai_example

    answer = {**ai_example(), "source": "BEDROCK", "modelId": "anthropic.claude", "zoneAnalysis": None}
    h = Harness(ai=answer)
    h.run_incident(anomaly_event(), ack_all(harness=h))
    diag = h.incident("EXC-0042")["aiDiagnosis"]
    assert diag == {"source": "BEDROCK", **{k: v for k, v in answer["diagnosis"].items()}}


def test_duplicate_events_do_not_open_a_second_incident_and_escalation_is_notified() -> None:
    h = Harness()
    with pytest.raises(Paused):
        h.run_incident(anomaly_event(), hold)
    runner, _ = h.run_incident(anomaly_event(), hold)
    assert runner.visited[-1] == "DuplicateIgnored"
    runner, _ = h.run_incident(anomaly_event(severity="CRITICAL"), hold)
    assert runner.visited[-2:] == ["NotifyEscalation", "DuplicateIgnored"]
    inc = h.incident("EXC-0042")
    assert inc["severity"] == "CRITICAL" and inc["GSI1SK"].startswith("1#") and inc["aiDiagnosis"]["stopMachine"]
    assert inc["timeline"][-1]["event"] == "ESCALATED"
    assert ("ESCALATED", "OPERATOR", "SMS") in h.published()
    assert len([k for k in h.table.items if k[0] == "MACHINE#EXC-0042" and k[1].startswith("INCIDENT#")]) == 1


def test_technician_timeout_reassigns_to_another_technician() -> None:
    h = Harness()
    calls = {"n": 0}

    def first_times_out(info):
        calls["n"] += 1
        return "TIMEOUT" if calls["n"] == 1 else ack_all(harness=h)(info)

    runner, _ = h.run_incident(anomaly_event(), first_times_out)
    assert runner.visited.count("AssignTechnician") == 2 and "ReleaseTechnician" in runner.visited
    inc = h.incident("EXC-0042")
    assert inc["assignedTech"] == "T-03"  # the other BEARING specialist
    assert [e["event"] for e in inc["activity"]].count("ACK_TIMEOUT") == 1
    assert ("TECH_TIMEOUT", "MAINT_LEAD", "IMMEDIATE") in h.published()
    assert h.table.get("TECH#T-07", "PROFILE")["availability"] == "AVAILABLE"


def test_no_technician_available_waits_and_retries() -> None:
    h = Harness()
    for (pk, _sk), item in list(h.table.items.items()):
        if pk.startswith("TECH#"):
            item["availability"] = "OFF"
    runner = Runner(definition("incident"), h.functions(), hold)
    with pytest.raises(RuntimeError):  # loops forever waiting for someone to come on shift
        runner.run(anomaly_event())
    assert runner.visited.count("WaitBeforeRetry") > 5 and "NotifyNoTechnician" in runner.visited


def test_zone_incident_when_three_machines_fail_in_the_same_zone() -> None:
    h = Harness()
    for mid, model in (("TRK-0103", "HT-793"), ("TRK-0107", "HT-793")):
        with pytest.raises(Paused):
            h.run_incident(anomaly_event(mid, model=model, signals=FUEL), hold)
    with pytest.raises(Paused):
        h.run_incident(anomaly_event("LDR-0012", model="LD-980", signals=FUEL), hold)

    zone_rows = [i for (pk, sk), i in h.table.items.items() if sk.startswith("ZINC#")]
    assert len(zone_rows) == 1
    zinc = zone_rows[0]
    assert zinc["machineIds"] == {"TRK-0103", "TRK-0107", "LDR-0012"}
    assert "FB-1120" in zinc["commonCause"]
    assert ("ZONE_INCIDENT", "SUPERVISOR", "IMMEDIATE") in h.published()
    assert ("ZONE_INCIDENT", "MANAGER", "IMMEDIATE") in h.published()
    for m in ("TRK-0103", "TRK-0107", "LDR-0012"):
        inc = h.incident(m)
        assert inc["zoneIncidentId"] == zinc["zoneIncidentId"] and "FB-1120" in inc["aiDiagnosis"]["zoneCause"]
        assert len(inc["correlatedIncidents"]) == 2
    assert h.incident("LDR-0012")["partsReserved"][0]["partId"] == "FLT-FUE-02"


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
    assert second["partsReserved"][0]["warehouse"] == "WH-NORTE"

    for day in (17, 18):  # WH-NORTE runs out after 2 bearings, then WH-SUR's single unit, then nothing
        h.clock.advance(days=1)
        runner, _ = h.run_incident(anomaly_event(detected_at=f"2026-11-{day}T14:00:00Z"), ack_all(harness=h))
    assert "NotifyRestock" in runner.visited
    assert ("RESTOCK", "MAINT_LEAD", "IMMEDIATE") in h.published()
