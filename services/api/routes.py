"""REST API for the web (contracts/api.md). Owner: Rol 2.

One Lambda behind API Gateway (HTTP API, JWT authorizer with Cognito). Routes are matched on method + path,
so it works with explicit routes or a single `ANY /{proxy+}` route. The Cognito group decides what each user
can call (ACCESS below). Storage keys and task tokens are never returned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from af_shared import keys
from af_shared.ddb import from_ddb, is_conditional_failure, iso, parse_iso, strip_keys
from af_shared.http import ApiError, decode_token, encode_token, groups
from boto3.dynamodb.conditions import Attr, Key

ALL = {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
STAFF = {"TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
HIDDEN = {"taskToken", "pendingSince", "anomaly", "reservations", "incidentQueue"}
MACHINE_FIELDS = (
    "machineId",
    "model",
    "machineType",
    "zoneId",
    "lat",
    "lon",
    "status",
    "healthScore",
    "severity",
    "lastSeen",
    "engineHours",
    "openIncidentId",
    "openIncidentSeverity",
)


class Ctx:
    def __init__(self, table: Any, sfn: Any, now: str, event: dict[str, Any], params: dict[str, str]) -> None:
        self.table, self.sfn, self.now, self.event, self.params = table, sfn, now, event, params
        self.query = event.get("queryStringParameters") or {}
        self.groups = groups(event)
        self.claims = (event.get("requestContext", {}).get("authorizer", {}).get("jwt") or {}).get("claims") or {}

    def body(self) -> dict[str, Any]:
        from af_shared.http import json_body

        return json_body(self.event)

    def limit(self, default: int, maximum: int) -> int:
        try:
            value = int(self.query.get("limit", default))
        except ValueError as exc:
            raise ApiError(400, "INVALID_LIMIT", "limit must be an integer") from exc
        return max(1, min(value, maximum))


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in strip_keys(item).items() if k not in HIDDEN}


def _query(ctx: Ctx, limit: int, **kwargs: Any) -> tuple[list[dict[str, Any]], str | None]:
    start = decode_token(ctx.query.get("nextToken"))
    if start:
        kwargs["ExclusiveStartKey"] = start
    page = ctx.table.query(Limit=limit, **kwargs)
    return page["Items"], encode_token(page.get("LastEvaluatedKey"))


def _page(items: list[dict[str, Any]], token: str | None) -> dict[str, Any]:
    return {"items": items, "count": len(items), "nextToken": token}


def _iso_param(ctx: Ctx, name: str, default: str) -> str:
    value = ctx.query.get(name) or default
    try:
        return iso(parse_iso(value))
    except ValueError as exc:
        raise ApiError(400, "INVALID_DATE", f"{name} must be ISO 8601 (e.g. 2026-11-13T14:05:00Z)") from exc


# ------------------------------------------------------------------------------------------------- handlers
def list_machines(ctx: Ctx) -> dict[str, Any]:
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#MACHINE")
    status = (ctx.query.get("status") or "").upper()
    if status:
        cond = cond & Key("GSI1SK").begins_with(f"{status}#")
    items, token = _query(ctx, ctx.limit(500, 1000), IndexName="GSI1", KeyConditionExpression=cond)
    machines = []
    for row in map(from_ddb, items):
        m = {f: row.get(f) for f in MACHINE_FIELDS}
        m["machineId"] = m["machineId"] or row["PK"].removeprefix("MACHINE#")
        machines.append(m)
    return _page(machines, token)


def get_machine(ctx: Ctx) -> dict[str, Any]:
    item = ctx.table.get_item(Key=keys.machine_profile(ctx.params["id"])).get("Item")
    if not item:
        raise ApiError(404, "MACHINE_NOT_FOUND", f"Machine {ctx.params['id']} not found")
    machine = _clean(item)
    machine.setdefault("machineId", ctx.params["id"])
    return machine


def list_readings(ctx: Ctx) -> dict[str, Any]:
    to = _iso_param(ctx, "to", ctx.now)
    since = _iso_param(ctx, "from", iso(parse_iso(to) - timedelta(hours=24)))
    if since > to:
        raise ApiError(400, "INVALID_RANGE", "from must be before to")
    cond = Key("PK").eq(keys.machine_pk(ctx.params["id"])) & Key("SK").between(
        keys.reading_sk(since), keys.reading_sk(to)
    )
    items, token = _query(ctx, ctx.limit(1440, 3000), KeyConditionExpression=cond)
    return {**_page([_clean(i) for i in items], token), "from": since, "to": to}


def _by_prefix(ctx: Ctx, prefix: str) -> dict[str, Any]:
    cond = Key("PK").eq(keys.machine_pk(ctx.params["id"])) & Key("SK").begins_with(prefix)
    items, token = _query(ctx, ctx.limit(50, 200), KeyConditionExpression=cond, ScanIndexForward=False)
    return _page([_clean(i) for i in items], token)


def machine_incidents(ctx: Ctx) -> dict[str, Any]:
    return _by_prefix(ctx, "INCIDENT#")


def machine_maintenance(ctx: Ctx) -> dict[str, Any]:
    return _by_prefix(ctx, "MAINT#")


def operation_incidents(ctx: Ctx) -> dict[str, Any]:
    state = (ctx.query.get("state") or "OPEN").upper()
    if state not in ("OPEN", "CLOSED"):
        raise ApiError(400, "INVALID_STATE", "state must be OPEN or CLOSED")
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#INCIDENT#{state}")
    # OPEN: severity rank ascending (CRITICAL first). CLOSED: same index, most severe first.
    items, token = _query(ctx, ctx.limit(100, 500), IndexName="GSI1", KeyConditionExpression=cond)
    return _page([_clean(i) for i in items], token)


def technicians(ctx: Ctx) -> dict[str, Any]:
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#TECH")
    availability = (ctx.query.get("availability") or "").upper()
    if availability:
        cond = cond & Key("GSI1SK").begins_with(f"{availability}#")
    items, token = _query(ctx, ctx.limit(200, 500), IndexName="GSI1", KeyConditionExpression=cond)
    return _page([_clean(i) for i in items], token)


def part_stock(ctx: Ctx) -> dict[str, Any]:
    part_id = ctx.params["partId"]
    rows = [from_ddb(i) for i in ctx.table.query(KeyConditionExpression=Key("PK").eq(f"PART#{part_id}"))["Items"]]
    meta = next((r for r in rows if r["SK"] == "META"), None)
    if meta is None:
        raise ApiError(404, "PART_NOT_FOUND", f"Part {part_id} not found")
    warehouses: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["SK"].startswith("STOCK#"):
            wh = r["SK"].split("#", 1)[1]
            warehouses.setdefault(wh, {"warehouseId": wh, "forecast": []}).update(
                {k: r.get(k) for k in ("qty", "available", "reserved", "updatedAt")}
            )
    for r in rows:
        if r["SK"].startswith("FORECAST#"):
            _, wh, week = r["SK"].split("#", 2)
            entry = {"week": week, **{k: v for k, v in _clean(r).items() if k not in ("partId", "warehouseId")}}
            warehouses.setdefault(wh, {"warehouseId": wh, "forecast": []})["forecast"].append(entry)
    return {"part": _clean(meta), "warehouses": sorted(warehouses.values(), key=lambda w: w["warehouseId"])}


def kpis(ctx: Ctx) -> dict[str, Any]:
    date = ctx.query.get("date") or iso(parse_iso(ctx.now) - timedelta(days=1))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ApiError(400, "INVALID_DATE", "date must be YYYY-MM-DD")
    cond = Key("GSI1PK").eq(f"OP#{ctx.params['op']}#KPI#{date}")
    items, token = _query(ctx, ctx.limit(1000, 1000), IndexName="GSI1", KeyConditionExpression=cond)
    rows = [{"machineId": i["PK"].removeprefix("MACHINE#"), **_clean(i)} for i in items]
    return {**_page(rows, token), "date": date}


def ack(ctx: Ctx) -> dict[str, Any]:
    """Technician confirms ARRIVED or RESOLVED; resumes the waiting Step Function (P5, P10)."""
    body = ctx.body()
    event = str(body.get("event", "")).upper()
    stage = {"ARRIVED": "ARRIVAL", "RESOLVED": "RESOLUTION"}.get(event)
    if stage is None:
        raise ApiError(400, "INVALID_EVENT", "event must be ARRIVED or RESOLVED")
    parts_used = body.get("partsUsed")
    if parts_used is not None and (
        not isinstance(parts_used, list)
        or not all(isinstance(p, dict) and p.get("partId") and int(p.get("qty", 0)) > 0 for p in parts_used)
    ):
        raise ApiError(400, "INVALID_PARTS", "partsUsed must be a list of {partId, qty>0}")

    machine_id, incident_id = ctx.params["id"], ctx.params["incId"]
    res = ctx.table.query(
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(machine_id)) & Key("SK").begins_with("INCIDENT#"),
        FilterExpression=Attr("incidentId").eq(incident_id),
    )
    if not res["Items"]:
        raise ApiError(404, "INCIDENT_NOT_FOUND", f"Incident {incident_id} not found for {machine_id}")
    incident = res["Items"][0]
    tech_claim = ctx.claims.get("custom:techId")
    if "MAINT_LEAD" not in ctx.groups and tech_claim and tech_claim != incident.get("assignedTech"):
        raise ApiError(403, "NOT_ASSIGNED", "Only the assigned technician or the maintenance lead can confirm")
    token = incident.get("taskToken")
    if incident.get("pendingStage") != stage or not token:
        expected = incident.get("pendingStage") or "none"
        raise ApiError(409, "WRONG_STAGE", f"Incident is not waiting for {event} (waiting for: {expected})")

    who = tech_claim or ctx.claims.get("cognito:username") or incident.get("assignedTech")
    output = {"event": event, "at": ctx.now, "by": who, "notes": body.get("notes")}
    if parts_used is not None:
        output["partsUsed"] = [{"partId": p["partId"], "qty": int(p["qty"])} for p in parts_used]
    new_state = "IN_PROGRESS" if event == "ARRIVED" else "RESOLVED"
    try:  # consume the token first, so a fast next stage can store its own token safely
        ctx.table.update_item(
            Key={"PK": incident["PK"], "SK": incident["SK"]},
            UpdateExpression="SET #st = :s, timeline = list_append(timeline, :ev) REMOVE taskToken, pendingStage",
            ConditionExpression="taskToken = :tok",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={
                ":s": new_state,
                ":tok": token,
                ":ev": [{"at": ctx.now, "event": event, "by": who}],
            },
        )
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
    return {"incidentId": incident_id, "state": new_state, "event": event, "at": ctx.now}


# ------------------------------------------------------------------------------------------------- routing
Route = tuple[str, re.Pattern[str], Callable[[Ctx], dict[str, Any]], set[str], int]
_ID = r"(?P<{}>[A-Za-z0-9_-]+)"


def _r(method: str, path: str, fn: Callable[[Ctx], dict[str, Any]], allowed: set[str], status: int = 200) -> Route:
    pattern = re.sub(r"\{(\w+)\}", lambda m: _ID.format(m.group(1)), path)
    return method, re.compile(f"^{pattern}/?$"), fn, allowed, status


ROUTES: list[Route] = [
    _r("GET", "/operations/{op}/machines", list_machines, ALL),
    _r("GET", "/machines/{id}", get_machine, ALL),
    _r("GET", "/machines/{id}/readings", list_readings, ALL),
    _r("GET", "/machines/{id}/incidents", machine_incidents, ALL),
    _r("GET", "/machines/{id}/maintenance", machine_maintenance, STAFF),
    _r("GET", "/operations/{op}/incidents", operation_incidents, ALL),
    _r("GET", "/operations/{op}/technicians", technicians, STAFF),
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
