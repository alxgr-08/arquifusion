"""API tests against the shapes of frontend/src/api/types.ts, including the technician ack that resumes the real
incident Step Function."""

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from af_shared.fakes import Recorder

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines" / "tests"))
from asl_runner import Paused  # noqa: E402
from auth import as_user  # noqa: E402
from flow_harness import Harness, anomaly_event  # noqa: E402

NOW = "2026-11-13T16:00:00Z"


def ts_fields(interface: str) -> set[str]:
    """Field names of an interface in frontend/src/api/types.ts (optional ones end with '?')."""
    src = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")
    body = re.search(rf"export interface {interface} \{{(.*?)\n\}}", src, re.S).group(1)
    return {m.group(1) + ("?" if m.group(2) else "") for m in re.finditer(r"^\s+(\w+)(\??):", body, re.M)}


def assert_shape(obj: dict, interface: str) -> None:
    fields = ts_fields(interface)
    required = {f for f in fields if not f.endswith("?")}
    allowed = {f.rstrip("?") for f in fields}
    assert required <= set(obj), f"{interface} missing {required - set(obj)}"
    assert set(obj) <= allowed, f"{interface} has unexpected {set(obj) - allowed}"


def req(
    method: str,
    path: str,
    claims: dict | None,
    query: dict | None = None,
    body: dict | None = None,
    headers: dict | None = None,
) -> dict:
    return {
        "rawPath": path,
        "headers": headers or {},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "http": {"method": method},
            "stage": "$default",
            "authorizer": {"jwt": {"claims": claims}} if claims else {},
        },
    }


@pytest.fixture
def api(load_handler):
    return load_handler("api")


def call(
    api, h: Harness, sfn, method: str, path: str, role: str | None = "SUPERVISOR", tech: str | None = None, **kwargs
) -> tuple[int, object]:
    claims = as_user(h.table, role, tech=tech) if role else None
    res = api.route(req(method, path, claims, **kwargs), h.table, sfn, NOW)
    return res["statusCode"], json.loads(res["body"])


def hold(_info):
    raise Paused()


class TechnicianInTheWeb:
    """on_wait handler: the technician presses the buttons in the web, which calls POST .../ack."""

    def __init__(self, api, h: Harness) -> None:
        self.api, self.h = api, h
        self.sfn = Recorder()
        self.responses: list[tuple[int, dict]] = []

    def __call__(self, info: dict) -> dict:
        inc = info["payload"]["incident"]
        action = "ARRIVED" if info["payload"]["stage"] == "ARRIVAL" else "RESOLVED"
        path = f"/machines/{inc['machineId']}/incidents/{inc['incidentId']}"
        tech = self.h.incident(inc["machineId"])["assignedTech"]
        claims = as_user(self.h.table, "TECHNICIAN", tech=tech)
        seen = self.api.route(req("GET", path, claims), self.h.table, self.sfn, NOW)  # the web reads the ETag
        assert seen["statusCode"] == 200 and seen["headers"]["ETag"]
        assert_shape(json.loads(seen["body"]), "Incident")
        posted = self.api.route(
            req("POST", path + "/ack", claims, body={"action": action}, headers={"if-match": seen["headers"]["ETag"]}),
            self.h.table,
            self.sfn,
            NOW,
        )
        status, out = posted["statusCode"], json.loads(posted["body"])
        self.responses.append((status, out))
        assert status == 200, out
        sent = self.sfn.of("send_task_success")[-1]
        assert sent["taskToken"] == info["token"]
        return json.loads(sent["output"])


def test_technician_acks_drive_the_step_function_and_return_the_incident(api) -> None:
    h = Harness()
    web = TechnicianInTheWeb(api, h)
    runner, _ = h.run_incident(anomaly_event(), web)
    assert runner.visited[-1] == "Done"
    assert [r[1]["state"] for r in web.responses] == ["IN_PROGRESS", "RESOLVED"]
    for _, incident in web.responses:
        assert_shape(incident, "Incident")
    status, history = call(api, h, Recorder(), "GET", "/machines/EXC-0042/incidents")
    closed = history[0]
    assert closed["state"] == "RESOLVED" and closed["downtimeMinutes"] >= 0
    assert [e["event"] for e in closed["timeline"]] == ["DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED"]
    status, maintenance = call(api, h, Recorder(), "GET", "/machines/EXC-0042/maintenance")
    assert_shape(maintenance[0], "Maintenance")
    assert maintenance[0]["partsReplaced"] == ["BRG-SPH-22"]


def test_read_endpoints_match_the_frontend_types(api) -> None:
    h = Harness()
    sfn = Recorder()
    with pytest.raises(Paused):
        h.run_incident(anomaly_event(severity="CRITICAL"), hold)
    for minute in range(3):
        ts = f"2026-11-13T15:5{minute}:00Z"
        h.table.put_item(
            Item={
                "PK": "MACHINE#EXC-0042",
                "SK": f"READING#{ts}",
                "messageId": "m",
                "operationId": "MINA-NORTE",
                "status": "OPERATING",
                "metrics": {"temperatureC": Decimal("92.4")},
                "healthScore": 40,
                "severity": "WARNING",
                "expiresAt": 1,
            }
        )
    h.table.put_item(
        Item={
            "PK": "MACHINE#EXC-0042",
            "SK": "KPI#2026-11-12",
            "GSI1PK": "OP#MINA-NORTE#KPI#2026-11-12",
            "GSI1SK": "EXC-0042",
            "machineId": "EXC-0042",
            "model": "EXC-320",
            "date": "2026-11-12",
            "utilizationPct": Decimal("81"),
            "operatingHours": Decimal("19.4"),
            "idleHours": Decimal("4.6"),
            "overuseHours": 0,
            "tonsMoved": 1800,
            "fuelLitersPerTon": Decimal("1.8"),
            "fuelLitersPerTonModel": Decimal("1.7"),
            "costPerHour": 190,
            "fuelAnomaly": False,
        }
    )
    h.table.put_item(Item={"PK": "PART#BRG-SPH-22", "SK": "FORECAST#WH-NORTE#2026-W47", "forecastQty": Decimal("2.4")})

    status, machines = call(api, h, sfn, "GET", "/operations/MINA-NORTE/machines")
    assert status == 200 and len(machines) == 12
    for m in machines:
        assert_shape(m, "Machine")
    status, operating = call(api, h, sfn, "GET", "/operations/MINA-NORTE/machines", query={"status": "OPERATING"})
    assert len(operating) == 12
    status, machine = call(api, h, sfn, "GET", "/machines/EXC-0042")
    assert machine["openIncidentId"] and machine["maintenanceIntervalHours"] == 500
    assert call(api, h, sfn, "GET", "/machines/NOPE-0000")[0] == 404

    status, readings = call(api, h, sfn, "GET", "/machines/EXC-0042/readings", query={"from": "2026-11-13T15:50:30Z"})
    assert [r["timestamp"] for r in readings] == ["2026-11-13T15:51:00Z", "2026-11-13T15:52:00Z"]
    assert_shape(readings[0], "Reading")

    status, incidents = call(api, h, sfn, "GET", "/operations/MINA-NORTE/incidents")
    assert len(incidents) == 1 and incidents[0]["severity"] == "CRITICAL" and incidents[0]["state"] == "ASSIGNED"
    assert_shape(incidents[0], "Incident")
    assert_shape(incidents[0]["aiDiagnosis"], "AiDiagnosis")
    assert incidents[0]["partsReserved"] == [{"partId": "BRG-SPH-22", "warehouse": "WH-NORTE", "qty": 1}]
    assert call(api, h, sfn, "GET", "/operations/MINA-NORTE/incidents", query={"state": "CLOSED"})[1] == []

    status, techs = call(api, h, sfn, "GET", "/operations/MINA-NORTE/technicians")
    for t in techs:
        assert_shape(t, "Technician")
    busy = [t for t in techs if t["availability"] == "BUSY"]
    assert [(t["techId"], t["queue"]) for t in busy] == [("T-07", 1)]

    status, parts = call(api, h, sfn, "GET", "/parts", role="MAINT_LEAD")
    assert {p["partId"] for p in parts} == {"FLT-HID-01", "BRG-SPH-22", "FLT-FUE-02", "CLT-RAD-05", "INJ-DSL-07"}
    bearing = next(p for p in parts if p["partId"] == "BRG-SPH-22")
    assert_shape(bearing, "PartStock")
    north = next(w for w in bearing["warehouses"] if w["warehouseId"] == "WH-NORTE")
    assert_shape(north, "WarehouseStock")
    assert (north["qty"], north["reserved"], north["forecastNext4Weeks"][0]) == (2, 1, 2)
    assert len(north["forecastNext4Weeks"]) == 4
    assert call(api, h, sfn, "GET", "/parts/BRG-SPH-22/stock", role="MAINT_LEAD")[1] == bearing
    assert call(api, h, sfn, "GET", "/parts/NOPE/stock", role="MAINT_LEAD")[0] == 404

    status, kpis = call(api, h, sfn, "GET", "/operations/MINA-NORTE/kpis")
    assert kpis[0]["fuelLitersPerTonFleet"] == 1.7
    assert ts_fields("MachineKpi") <= set(kpis[0])
    assert call(api, h, sfn, "GET", "/operations/MINA-NORTE/kpis", role="OPERATOR")[0] == 403


def test_errors_auth_and_cors(api) -> None:
    h = Harness()
    sfn = Recorder()
    assert call(api, h, sfn, "GET", "/nope")[0] == 404
    assert call(api, h, sfn, "DELETE", "/machines/EXC-0042")[0] == 405
    assert call(api, h, sfn, "GET", "/machines/EXC-0042", role=None)[0] == 401
    assert call(api, h, sfn, "GET", "/machines/EXC-0042/readings", query={"from": "yesterday"})[0] == 400
    res = api.route(req("OPTIONS", "/machines/EXC-0042", None), h.table, sfn, NOW)
    assert res["statusCode"] == 204 and res["headers"]["Access-Control-Allow-Origin"] == "*"
