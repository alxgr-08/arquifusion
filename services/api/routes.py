"""REST API for the web. Response shapes = frontend/src/api/types.ts (Rol 3); routes = contracts/api.md. Owner: Rol 2.

One Lambda behind API Gateway (HTTP API, JWT authorizer with Cognito). Routes are matched on method + path, so it
works with explicit routes or a single `ANY /{proxy+}` route. The Cognito group decides what each user can call.
Lists are plain JSON arrays (the web expects `Machine[]`, `Incident[]`, ...). Storage keys and task tokens are never
returned.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from af_shared import keys
from af_shared.ddb import from_ddb, is_conditional_failure, iso, parse_iso
from af_shared.http import ApiError, groups
from boto3.dynamodb.conditions import Attr, Key

ALL = {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
STAFF = {"TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
SEV_RANK = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "OK": 3}
TIMELINE_EVENTS = {"DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED", "ESCALATED"}
MAX_ITEMS = 2000


class Ctx:
    def __init__(self, table: Any, sfn: Any, now: str, event: dict[str, Any], params: dict[str, str]) -> None:
        self.table, self.sfn, self.now, self.event, self.params = table, sfn, now, event, params
        self.query = event.get("queryStringParameters") or {}
        self.groups = groups(event)
        self.claims = (event.get("requestContext", {}).get("authorizer", {}).get("jwt") or {}).get("claims") or {}

    def body(self) -> dict[str, Any]:
        from af_shared.http import json_body

        return json_body(self.event)


def _query_all(table: Any, limit: int = MAX_ITEMS, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        page = table.query(**kwargs)
        items += page["Items"]
        if not page.get("LastEvaluatedKey") or len(items) >= limit:
            return [from_ddb(i) for i in items[:limit]]
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _iso_param(ctx: Ctx, name: str, default: str) -> str:
    value = ctx.query.get(name) or default
    try:
        return iso(parse_iso(value))
    except ValueError as exc:
        raise ApiError(400, "INVALID_DATE", f"{name} must be ISO 8601 (e.g. 2026-11-13T14:05:00Z)") from exc


def _pick(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: row[f] for f in fields if row.get(f) is not None}


# ------------------------------------------------------------------------------------------------- mappers
MACHINE_FIELDS = (
    "machineId",
    "model",
    "machineType",
    "operationId",
    "zoneId",
    "lat",
    "lon",
    "status",
    "healthScore",
    "severity",
    "lastSeen",
    "engineHours",
    "lastMaintenanceHours",
    "maintenanceIntervalHours",
    "openIncidentId",
)


def machine_out(row: dict[str, Any]) -> dict[str, Any]:
    out = _pick(row, MACHINE_FIELDS)
    out.setdefault("machineId", row["PK"].removeprefix("MACHINE#"))
    return out


def incident_out(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state", "OPEN")
    if row.get("GSI1PK", "").endswith("#CLOSED") or state == "CLOSED":
        state = "RESOLVED"
    elif state not in ("OPEN", "ASSIGNED", "IN_PROGRESS", "RESOLVED"):
        state = "OPEN"
    out = _pick(
        row,
        (
            "incidentId",
            "machineId",
            "operationId",
            "zoneId",
            "severity",
            "createdAt",
            "healthScore",
            "aiDiagnosis",
            "messages",
            "assignedTech",
            "etaMinutes",
            "downtimeMinutes",
            "recurrenceOf",
            "zoneIncidentId",
        ),
    )
    out.update(
        {
            "state": state,
            "signals": row.get("signals", []),
            "partsReserved": [
                {"partId": p["partId"], "warehouse": p.get("warehouse") or p.get("warehouseId"), "qty": p["qty"]}
                for p in row.get("partsReserved", [])
            ],
            "timeline": [
                _pick(e, ("at", "event", "tech")) for e in row.get("timeline", []) if e.get("event") in TIMELINE_EVENTS
            ],
            "correlatedIncidents": row.get("correlatedIncidents", []),
        }
    )
    out.setdefault("machineId", row["PK"].removeprefix("MACHINE#"))
    return out


def maintenance_out(row: dict[str, Any]) -> dict[str, Any]:
    parts = row.get("partsReplaced")
    if parts is None:  # older records
        parts = [p["partId"] for p in row.get("partsUsed", row.get("partsPlanned", []))]
    return {
        "maintenanceId": row.get("maintenanceId"),
        "date": row.get("date"),
        "type": row.get("type"),
        "engineHours": row.get("engineHours"),
        "partsReplaced": parts,
        "notes": row.get("notes", ""),
    }


def technician_out(row: dict[str, Any]) -> dict[str, Any]:
    out = _pick(row, ("techId", "name", "specialties", "availability", "lat", "lon"))
    out["queue"] = int(row.get("queue", 0))
    return out


KPI_FIELDS = (
    "machineId",
    "model",
    "date",
    "utilizationPct",
    "idleHours",
    "overuseHours",
    "tonsMoved",
    "fuelLitersPerTon",
    "costPerHour",
    "fuelAnomaly",
    "operatingHours",
    "clusterLabel",
    "progressiveFuelIncrease",
    "fuelTrendPctPerWeek",
)


def kpi_out(row: dict[str, Any]) -> dict[str, Any]:
    out = _pick(row, KPI_FIELDS)
    out.setdefault("machineId", row["PK"].removeprefix("MACHINE#"))
    out["fuelLitersPerTonFleet"] = row.get("fuelLitersPerTonFleet", row.get("fuelLitersPerTonModel"))
    out["fuelAnomaly"] = bool(row.get("fuelAnomaly", False))
    return out


def part_out(table: Any, part_id: str, now: str) -> dict[str, Any] | None:
    rows = _query_all(table, KeyConditionExpression=Key("PK").eq(f"PART#{part_id}"))
    meta = next((r for r in rows if r["SK"] == "META"), None)
    stocks = [r for r in rows if r["SK"].startswith("STOCK#")]
    if meta is None and not stocks:
        return None
    meta = meta or stocks[0]
    start = parse_iso(now)
    weeks = []
    for i in range(1, 5):
        cal = (start + timedelta(weeks=i)).isocalendar()
        weeks.append(f"{cal.year}-W{cal.week:02d}")
    forecast = {
        r["SK"].split("#", 2)[1] + "#" + r["SK"].split("#", 2)[2]: r for r in rows if r["SK"].startswith("FORECAST#")
    }
    total = sum(int(s.get("qty", 0)) for s in stocks) or 1
    warehouses = []
    for s in sorted(stocks, key=lambda r: r["SK"]):
        wh = s["SK"].split("#", 1)[1]
        fallback = float(meta.get("dailyDemand", 0)) * 7 * int(s.get("qty", 0)) / total  # until BigQuery runs
        series = []
        for week in weeks:
            f = forecast.get(f"{wh}#{week}")
            series.append(round(float(f.get("forecastQty", 0))) if f else round(fallback))
        warehouses.append(
            {
                "warehouseId": wh,
                "qty": int(s.get("qty", 0)),
                "reserved": int(s.get("reserved", 0)),
                "reorderPoint": int(s.get("reorderPoint", 1)),
                "forecastNext4Weeks": series,
            }
        )
    return {
        "partId": part_id,
        "name": meta.get("name", part_id),
        "component": meta.get("component", "UNKNOWN"),
        "warehouses": warehouses,
    }


# ------------------------------------------------------------------------------------------------- handlers
def list_machines(ctx: Ctx) -> list[dict[str, Any]]:
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#MACHINE")
    status = (ctx.query.get("status") or "").upper()
    if status:
        cond = cond & Key("GSI1SK").begins_with(f"{status}#")
    return [machine_out(r) for r in _query_all(ctx.table, IndexName="GSI1", KeyConditionExpression=cond)]


def get_machine(ctx: Ctx) -> dict[str, Any]:
    item = ctx.table.get_item(Key=keys.machine_profile(ctx.params["id"])).get("Item")
    if not item:
        raise ApiError(404, "MACHINE_NOT_FOUND", f"Machine {ctx.params['id']} not found")
    return machine_out(from_ddb(item))


def list_readings(ctx: Ctx) -> list[dict[str, Any]]:
    to = _iso_param(ctx, "to", ctx.now)
    since = _iso_param(ctx, "from", iso(parse_iso(to) - timedelta(hours=24)))
    if since > to:
        raise ApiError(400, "INVALID_RANGE", "from must be before to")
    cond = Key("PK").eq(keys.machine_pk(ctx.params["id"])) & Key("SK").between(
        keys.reading_sk(since), keys.reading_sk(to)
    )
    return [
        {"timestamp": r["SK"].removeprefix("READING#"), **_pick(r, ("healthScore", "severity", "metrics"))}
        for r in _query_all(ctx.table, KeyConditionExpression=cond)
    ]


def machine_incidents(ctx: Ctx) -> list[dict[str, Any]]:
    cond = Key("PK").eq(keys.machine_pk(ctx.params["id"])) & Key("SK").begins_with("INCIDENT#")
    return [incident_out(r) for r in _query_all(ctx.table, 200, KeyConditionExpression=cond, ScanIndexForward=False)]


def machine_maintenance(ctx: Ctx) -> list[dict[str, Any]]:
    cond = Key("PK").eq(keys.machine_pk(ctx.params["id"])) & Key("SK").begins_with("MAINT#")
    return [maintenance_out(r) for r in _query_all(ctx.table, 200, KeyConditionExpression=cond, ScanIndexForward=False)]


def operation_incidents(ctx: Ctx) -> list[dict[str, Any]]:
    state = (ctx.query.get("state") or "OPEN").upper()
    if state not in ("OPEN", "CLOSED"):
        raise ApiError(400, "INVALID_STATE", "state must be OPEN or CLOSED")
    rows = _query_all(
        ctx.table, IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"OP#{ctx.params['op']}#INCIDENT#{state}")
    )
    out = [incident_out(r) for r in rows if r.get("incidentId")]
    out.sort(key=lambda i: i["createdAt"], reverse=True)
    out.sort(key=lambda i: SEV_RANK.get(i["severity"], 9))  # stable: CRITICAL first, newest first inside
    return out


def technicians(ctx: Ctx) -> list[dict[str, Any]]:
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#TECH")
    availability = (ctx.query.get("availability") or "").upper()
    if availability:
        cond = cond & Key("GSI1SK").begins_with(f"{availability}#")
    return [technician_out(r) for r in _query_all(ctx.table, IndexName="GSI1", KeyConditionExpression=cond)]


def list_parts(ctx: Ctx) -> list[dict[str, Any]]:
    op = ctx.query.get("operationId") or os.environ.get("AF_OPERATION_ID", "MINA-NORTE")
    meta = from_ddb(ctx.table.get_item(Key={"PK": f"OP#{op}", "SK": "META"}).get("Item") or {})
    part_ids: set[str] = set()
    for wh in meta.get("warehouses", []):
        for row in _query_all(ctx.table, IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"WH#{wh}")):
            part_ids.add(row["GSI1SK"].removeprefix("PART#"))
    return [p for p in (part_out(ctx.table, pid, ctx.now) for pid in sorted(part_ids)) if p]


def part_stock(ctx: Ctx) -> dict[str, Any]:
    part = part_out(ctx.table, ctx.params["partId"], ctx.now)
    if part is None:
        raise ApiError(404, "PART_NOT_FOUND", f"Part {ctx.params['partId']} not found")
    return part


def kpis(ctx: Ctx) -> list[dict[str, Any]]:
    date = ctx.query.get("date") or iso(parse_iso(ctx.now) - timedelta(days=1))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ApiError(400, "INVALID_DATE", "date must be YYYY-MM-DD")
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#KPI#{date}")
    return [kpi_out(r) for r in _query_all(ctx.table, IndexName="GSI1", KeyConditionExpression=cond)]


def ack(ctx: Ctx) -> dict[str, Any]:
    """Technician confirms ARRIVED or RESOLVED; resumes the waiting Step Function. Returns the updated Incident."""
    body = ctx.body()
    action = str(body.get("action") or body.get("event") or "").upper()
    stage = {"ARRIVED": "ARRIVAL", "RESOLVED": "RESOLUTION"}.get(action)
    if stage is None:
        raise ApiError(400, "INVALID_ACTION", "action must be ARRIVED or RESOLVED")
    parts_used = body.get("partsUsed")
    if parts_used is not None and (
        not isinstance(parts_used, list)
        or not all(isinstance(p, dict) and p.get("partId") and int(p.get("qty", 0)) > 0 for p in parts_used)
    ):
        raise ApiError(400, "INVALID_PARTS", "partsUsed must be a list of {partId, qty>0}")

    machine_id, incident_id = ctx.params["id"], ctx.params["incId"]
    rows = _query_all(
        ctx.table,
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(machine_id)) & Key("SK").begins_with("INCIDENT#"),
        FilterExpression=Attr("incidentId").eq(incident_id),
    )
    if not rows:
        raise ApiError(404, "INCIDENT_NOT_FOUND", f"Incident {incident_id} not found for {machine_id}")
    incident = rows[0]
    tech_claim = ctx.claims.get("custom:techId")
    if "MAINT_LEAD" not in ctx.groups and tech_claim and tech_claim != incident.get("assignedTech"):
        raise ApiError(403, "NOT_ASSIGNED", "Only the assigned technician or the maintenance lead can confirm")
    token = incident.get("taskToken")
    if incident.get("pendingStage") != stage or not token:
        expected = incident.get("pendingStage") or "none"
        raise ApiError(409, "WRONG_STAGE", f"Incident is not waiting for {action} (waiting for: {expected})")

    output = {
        "event": action,
        "at": ctx.now,
        "by": tech_claim or incident.get("assignedTech"),
        "notes": body.get("notes"),
    }
    if parts_used is not None:
        output["partsUsed"] = [{"partId": p["partId"], "qty": int(p["qty"])} for p in parts_used]
    new_state = "IN_PROGRESS" if action == "ARRIVED" else "RESOLVED"
    key = {"PK": incident["PK"], "SK": incident["SK"]}
    try:  # consume the token first, so a fast next stage can store its own token safely
        updated = ctx.table.update_item(
            Key=key,
            UpdateExpression="SET #st = :s, timeline = list_append(timeline, :ev) REMOVE taskToken, pendingStage",
            ConditionExpression="taskToken = :tok",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={
                ":s": new_state,
                ":tok": token,
                ":ev": [
                    {
                        "at": ctx.now,
                        "event": action,
                        **({"tech": incident["assignedTech"]} if incident.get("assignedTech") else {}),
                    }
                ],
            },
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except Exception as exc:
        if is_conditional_failure(exc):
            raise ApiError(409, "ALREADY_CONFIRMED", "This step was already confirmed") from exc
        raise
    try:
        ctx.sfn.send_task_success(taskToken=token, output=json.dumps(output))
    except Exception as exc:
        code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", type(exc).__name__)
        if code in ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist"):
            raise ApiError(409, "TASK_EXPIRED", "The wait expired; the incident was reassigned") from exc
        raise
    return incident_out(from_ddb(updated))


# ------------------------------------------------------------------------------------------------- routing
Route = tuple[str, re.Pattern[str], Callable[[Ctx], Any], set[str], int]
_ID = r"(?P<{}>[A-Za-z0-9_-]+)"


def _r(method: str, path: str, fn: Callable[[Ctx], Any], allowed: set[str], status: int = 200) -> Route:
    pattern = re.sub(r"\{(\w+)\}", lambda m: _ID.format(m.group(1)), path)
    return method, re.compile(f"^{pattern}/?$"), fn, allowed, status


ROUTES: list[Route] = [
    _r("GET", "/operations/{op}/machines", list_machines, ALL),
    _r("GET", "/machines/{id}", get_machine, ALL),
    _r("GET", "/machines/{id}/readings", list_readings, ALL),
    _r("GET", "/machines/{id}/incidents", machine_incidents, ALL),
    _r("GET", "/machines/{id}/maintenance", machine_maintenance, ALL),
    _r("GET", "/operations/{op}/incidents", operation_incidents, ALL),
    _r("GET", "/operations/{op}/technicians", technicians, ALL),
    _r("GET", "/parts", list_parts, STAFF),
    _r("GET", "/parts/{partId}/stock", part_stock, STAFF),
    _r("GET", "/operations/{op}/kpis", kpis, {"MAINT_LEAD", "SUPERVISOR", "MANAGER"}),
    _r("POST", "/machines/{id}/incidents/{incId}/ack", ack, {"TECHNICIAN", "MAINT_LEAD"}),
]


def match(method: str, path: str) -> tuple[Route, dict[str, str]]:
    path_found = False
    for route in ROUTES:
        m = route[1].match(path)
        if m:
            path_found = True
            if route[0] == method:
                return route, m.groupdict()
    if path_found:
        raise ApiError(405, "METHOD_NOT_ALLOWED", f"{method} not allowed on {path}")
    raise ApiError(404, "ROUTE_NOT_FOUND", f"No route for {method} {path}")
