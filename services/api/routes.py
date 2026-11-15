"""REST API for the web. Response shapes = frontend/src/api/types.ts (Rol 3); routes = contracts/api.md. Owner: Rol 2.

One Lambda behind API Gateway (HTTP API, JWT authorizer for Google). Routes are matched on method + path, so it
works with explicit routes or a single `ANY /{proxy+}` route. The caller's grant (af_shared.grants: role per
operation, kept in DynamoDB) decides what each user can call and in which operation.
Lists are plain JSON arrays (the web expects `Machine[]`, `Incident[]`, ...). Storage keys and task tokens are never
returned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from af_shared import grants, keys
from af_shared.ddb import (
    from_ddb,
    is_conditional_failure,
    is_transaction_conditional_failure,
    iso,
    parse_iso,
    transact_write,
)
from af_shared.http import ApiError, Reply, header
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
        self.principal: grants.Principal | None = None  # None only with AF_AUTH_DISABLED (local tests)

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


ACK_STAGES = {"ARRIVED": "ARRIVAL", "RESOLVED": "RESOLUTION"}
LEGACY_WAIT = "legacy"  # a wait stored before v1.1 (no generation): it needs reconciliation, never a blind ack


def _find_incident(ctx: Ctx, machine_id: str, incident_id: str) -> dict[str, Any]:
    rows = _query_all(
        ctx.table,
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(machine_id)) & Key("SK").begins_with("INCIDENT#"),
        FilterExpression=Attr("incidentId").eq(incident_id),
    )
    if not rows:
        raise ApiError(404, "INCIDENT_NOT_FOUND", f"Incident {incident_id} not found for {machine_id}")
    return rows[0]


def _reload(ctx: Ctx, incident: dict[str, Any]) -> dict[str, Any]:
    item = ctx.table.get_item(Key={"PK": incident["PK"], "SK": incident["SK"]}, ConsistentRead=True).get("Item")
    return from_ddb(item or incident)


def operation_of(ctx: Ctx) -> str | None:
    """Operation a request touches: the {op} in the path or the machine's operation (None = not scoped)."""
    if "op" in ctx.params:
        return ctx.params["op"]
    if "id" in ctx.params:
        row = ctx.table.get_item(Key=keys.machine_profile(ctx.params["id"])).get("Item")
        if row:
            return str(row.get("operationId"))
    return None


def _actor(ctx: Ctx, grant: grants.Grant | None, incident: dict[str, Any]) -> dict[str, Any]:
    """Who really confirms (also when a lead confirms for the technician). Internal: never in the DTO."""
    if grant is None:  # AF_AUTH_DISABLED (local tests only)
        return {"sub": "local", "role": "LOCAL", "techId": incident.get("assignedTech")}
    return {"iss": grant.iss, "sub": grant.sub, "role": grant.role, "techId": grant.tech_id}


def _authorize_ack(ctx: Ctx, incident: dict[str, Any]) -> tuple[str, grants.Grant | None]:
    """Grant in the incident's operation; technicians only for incidents assigned to them. Returns (who, grant)."""
    if ctx.principal is None:  # AF_AUTH_DISABLED
        return incident.get("assignedTech") or "LOCAL", None
    grant = ctx.principal.grant_for(str(incident.get("operationId")))
    if grant is None:
        raise ApiError(403, "OTHER_OPERATION", "No access to the incident's operation")
    if grant.role == "MAINT_LEAD":
        return grant.tech_id or "MAINT_LEAD", grant
    if grant.role != "TECHNICIAN":
        raise ApiError(403, "FORBIDDEN", "Only the assigned technician or the maintenance lead can confirm")
    if not grant.tech_id:
        raise ApiError(403, "NO_TECH_ID", "The technician grant has no techId")
    if grant.tech_id != incident.get("assignedTech"):
        raise ApiError(403, "NOT_ASSIGNED", "Only the assigned technician or the maintenance lead can confirm")
    return grant.tech_id, grant


def current_generation(incident: dict[str, Any]) -> str | None:
    """Version of the wait the incident is in now (None = not waiting for a confirmation)."""
    if incident.get("taskToken") and incident.get("pendingStage") and not incident.get("flowFailed"):
        return incident.get("waitGeneration") or LEGACY_WAIT
    return None


def etag(incident: dict[str, Any]) -> dict[str, str]:
    gen = current_generation(incident)
    return {"ETag": f'"{gen}"'} if gen and gen != LEGACY_WAIT else {}


def _if_match(ctx: Ctx) -> str:
    raw = header(ctx.event, "If-Match")
    if not raw:
        raise ApiError(428, "PRECONDITION_REQUIRED", "Send If-Match with the ETag of GET .../incidents/{incId}")
    value = raw.strip().removeprefix("W/").strip().strip('"')
    if not value or value == "*":
        raise ApiError(428, "PRECONDITION_REQUIRED", "If-Match must be the ETag of the incident, not *")
    return value


def _command(body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """(action, stage, normalized command). Keeps {action} and the legacy alias {event}."""
    action = str(body.get("action") or body.get("event") or "").upper()
    stage = ACK_STAGES.get(action)
    if stage is None:
        raise ApiError(400, "INVALID_ACTION", "action must be ARRIVED or RESOLVED")
    parts_used = body.get("partsUsed")
    if parts_used is not None:
        try:
            ok = isinstance(parts_used, list) and all(
                isinstance(p, dict) and p.get("partId") and int(p.get("qty", 0)) > 0 for p in parts_used
            )
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise ApiError(400, "INVALID_PARTS", "partsUsed must be a list of {partId, qty>0}")
        parts_used = sorted(
            ({"partId": str(p["partId"]), "qty": int(p["qty"])} for p in parts_used),
            key=lambda p: (p["partId"], p["qty"]),
        )
    notes = body.get("notes")
    command = {"action": action, "partsUsed": parts_used, "notes": None if notes is None else str(notes)}
    return action, stage, command


def _fingerprint(command: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(command, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _receipt_key(incident: dict[str, Any], stage: str, generation: str) -> dict[str, str]:
    return {"PK": incident["PK"], "SK": f"ACK#{incident['incidentId']}#{stage}#{generation}"}


def _get_receipt(ctx: Ctx, key: dict[str, str]) -> dict[str, Any] | None:
    item = ctx.table.get_item(Key=key, ConsistentRead=True).get("Item")
    return from_ddb(item) if item else None


def _mark(ctx: Ctx, key: dict[str, str], status: str) -> None:
    try:
        ctx.table.update_item(
            Key=key,
            UpdateExpression="SET #s = :s, settledAt = :now",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":pending": "PENDING", ":now": ctx.now},
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise


def _view(incident: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Incident DTO after a delivered ack. The workflow writes the timeline a moment later (CertifyArrival /
    CloseIncident); until then the answer shows the confirmed effect of this ack."""
    out = incident_out(incident)
    certified = (incident.get("certified") or {}).get(receipt["stage"])
    if not certified:
        event = {"at": receipt["at"], "event": receipt["action"]}
        if incident.get("assignedTech"):
            event["tech"] = incident["assignedTech"]
        out["timeline"] = [*out["timeline"], event]
        out["state"] = "IN_PROGRESS" if receipt["action"] == "ARRIVED" else "RESOLVED"
    return out


def _evidence(incident: dict[str, Any], receipt: dict[str, Any]) -> str:
    """What the stored data proves about a receipt whose SendTaskSuccess failed or whose answer was lost.

    ACCEPTED: the execution certified this ackId. REJECTED: the wait is over and the execution certified another
    ack, failed, or moved to a newer wait without certifying ours. PENDING: not enough evidence yet (retry).
    InvalidToken alone proves nothing: only the certification written by the workflow counts.
    """
    certified = (incident.get("certified") or {}).get(receipt["stage"]) or {}
    if certified.get("ackId") == receipt["ackId"]:
        return "ACCEPTED"
    if certified or incident.get("flowFailed"):
        return "REJECTED"
    if current_generation(incident) == receipt["generation"]:
        return "PENDING"
    # Our wait is gone and nobody certified this stage: a newer wait was registered (time-out/reassignment;
    # an accepted ack is always certified before the next wait exists) or the incident was closed elsewhere.
    return "REJECTED"


def get_incident(ctx: Ctx) -> Reply:
    """One incident with ETag = version of the wait it is in now (send it back as If-Match in POST .../ack)."""
    incident = _find_incident(ctx, ctx.params["id"], ctx.params["incId"])
    return Reply(incident_out(incident), headers=etag(incident))


def ack(ctx: Ctx) -> Reply:
    """Technician (or lead) confirms ARRIVED / RESOLVED of the wait they saw (If-Match = its ETag).

    1. A durable receipt ACK#{incId}#{stage}#{generation} is claimed in one transaction with the grant (role,
       techId, version) and the wait (generation, stage, assigned tech). Same actor + same body = same receipt.
    2. SendTaskSuccess with the receipt's token and payload (ackId, generation, execution, actor).
    3. If that fails or the answer is lost, the workflow's certification decides (see _evidence).
    """
    action, stage, command = _command(ctx.body())
    wanted = _if_match(ctx)
    incident = _find_incident(ctx, ctx.params["id"], ctx.params["incId"])
    who, grant = _authorize_ack(ctx, incident)
    actor = _actor(ctx, grant, incident)
    fingerprint = _fingerprint(command)
    key = _receipt_key(incident, stage, wanted)
    receipt = _get_receipt(ctx, key)

    if receipt is None:
        current = current_generation(incident)
        if current == LEGACY_WAIT:
            raise ApiError(
                409,
                "WAIT_NEEDS_RECONCILIATION",
                "This wait has no version (registered before v1.2); it must be reconciled first",
            )
        if current is None or incident.get("pendingStage") != stage:
            if current is not None and current != wanted:
                raise ApiError(412, "STALE_WAIT", "The incident changed; reload it", etag(incident))
            waiting = incident.get("pendingStage") or "none"
            raise ApiError(
                409, "WRONG_STAGE", f"Incident is not waiting for {action} (waiting for: {waiting})", etag(incident)
            )
        if current != wanted:
            raise ApiError(412, "STALE_WAIT", "The incident changed; reload it", etag(incident))
        ack_id = hashlib.sha256(f"{incident['incidentId']}|{stage}|{wanted}".encode()).hexdigest()[:20]
        payload = {
            "event": action,
            "at": ctx.now,
            "by": who,
            "notes": command["notes"],
            "ackId": ack_id,
            "generation": wanted,
            "executionId": incident.get("waitExecution"),
            "actor": actor,
        }
        if command["partsUsed"] is not None:
            payload["partsUsed"] = command["partsUsed"]
        receipt = {
            **key,
            "ackId": ack_id,
            "incidentId": incident["incidentId"],
            "stage": stage,
            "action": action,
            "generation": wanted,
            "executionId": incident.get("waitExecution"),
            "token": incident["taskToken"],
            "actor": actor,
            "fingerprint": fingerprint,
            "at": ctx.now,
            "payload": json.dumps(payload, ensure_ascii=False),
            "status": "PENDING",
        }
        wait_cond = "waitGeneration = :gen AND pendingStage = :stage AND taskToken = :tok"
        wait_values: dict[str, Any] = {":gen": wanted, ":stage": stage, ":tok": incident["taskToken"]}
        if grant is not None and grant.role == "TECHNICIAN":
            wait_cond += " AND assignedTech = :tech"
            wait_values[":tech"] = grant.tech_id
        actions = [grants.condition_check(grant)] if grant else []
        actions += [
            {
                "ConditionCheck": {
                    "Key": {"PK": incident["PK"], "SK": incident["SK"]},
                    "ConditionExpression": wait_cond,
                    "ExpressionAttributeValues": wait_values,
                }
            },
            {"Put": {"Item": receipt, "ConditionExpression": "attribute_not_exists(PK)"}},
        ]
        try:
            transact_write(ctx.table, actions)
        except Exception as exc:
            if not (is_transaction_conditional_failure(exc) or is_conditional_failure(exc)):
                raise
            reasons = [r.get("Code") for r in (getattr(exc, "response", None) or {}).get("CancellationReasons") or []]
            offset = 1 if grant else 0
            if grant and reasons[:1] == ["ConditionalCheckFailed"]:
                raise ApiError(403, "GRANT_CHANGED", "Your access changed; reload and try again") from exc
            if len(reasons) > offset and reasons[offset] == "ConditionalCheckFailed":
                fresh = _reload(ctx, incident)
                if grant and grant.role == "TECHNICIAN" and fresh.get("assignedTech") != grant.tech_id:
                    raise ApiError(403, "NOT_ASSIGNED", "The incident was reassigned") from exc
                raise ApiError(412, "STALE_WAIT", "The incident changed; reload it", etag(fresh)) from exc
            receipt = _get_receipt(ctx, key)  # a concurrent click stored it first: continue with that one
            if receipt is None:
                raise

    if receipt["actor"].get("sub") != actor.get("sub"):
        raise ApiError(409, "ACK_BY_OTHER", "This step was already confirmed by another user")
    if receipt["fingerprint"] != fingerprint:
        raise ApiError(409, "ACK_CONFLICT", "This step was already confirmed with other parts or notes")

    if receipt["status"] in ("ACCEPTED", "DELIVERED"):
        return Reply(_view(_reload(ctx, incident), receipt), headers=etag(_reload(ctx, incident)))
    if receipt["status"] == "REJECTED":
        raise ApiError(409, "TASK_EXPIRED", "The wait expired or changed; reload the incident", etag(incident))

    try:
        ctx.sfn.send_task_success(taskToken=receipt["token"], output=receipt["payload"])
    except Exception as exc:
        fresh = _reload(ctx, incident)
        verdict = _evidence(fresh, receipt)
        if verdict == "ACCEPTED":
            _mark(ctx, key, "ACCEPTED")
            return Reply(_view(fresh, receipt), headers=etag(fresh))
        if verdict == "REJECTED":
            _mark(ctx, key, "REJECTED")
            raise ApiError(
                409, "TASK_EXPIRED", "The wait expired or changed; reload the incident", etag(fresh)
            ) from exc
        raise ApiError(
            503,
            "CALLBACK_PENDING",
            "Confirmation stored; repeat the same request (same If-Match and body)",
            {"Retry-After": "2"},
        ) from exc
    _mark(ctx, key, "DELIVERED")
    fresh = _reload(ctx, incident)
    return Reply(_view(fresh, receipt), headers=etag(fresh))


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
    _r("GET", "/machines/{id}/incidents/{incId}", get_incident, ALL),
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
