"""Incident lifecycle tasks for the af-dev-incident Step Function (P3, P10, P11). Owner: Rol 2.

Every function is (table, payload, now) -> JSON-able dict, so the Step Function state stays small and the
logic is testable with af_shared.fakes.FakeTable. Writes are conditional so Step Functions retries are safe.
"""

from __future__ import annotations

import secrets
from typing import Any

from af_shared import AiDiagnosisResponse, AnomalyDetected, keys, techs
from af_shared.ddb import from_ddb, is_conditional_failure, minus, parse_iso, strip_keys, to_ddb
from af_shared.fleet import haversine_km
from boto3.dynamodb.conditions import Key
from pydantic import ValidationError

ROLES = ("OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER")
SEVERITY_ORDER = {"OK": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}


class TaskError(Exception):
    """Raised for unrecoverable input problems; the Step Function catches it and releases the claim."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(2)}"


def _timeline(event: str, at: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"at": at, "event": event, **{k: v for k, v in extra.items() if v is not None}}]


def _incident_key(machine_id: str, incident_sk: str) -> dict[str, str]:
    return {"PK": keys.machine_pk(machine_id), "SK": incident_sk}


# ----------------------------------------------------------------------------------------------- claim
def claim(table: Any, payload: dict[str, Any], now: str, settings: dict[str, Any]) -> dict[str, Any]:
    """ValidateEvent: validate AnomalyDetected and atomically claim 'one open incident per machine'.

    The health-evaluator emits AnomalyDetected on every WARNING/CRITICAL reading, so most executions are
    duplicates: they end here (and only escalate severity when it got worse).
    """
    try:
        event = AnomalyDetected.model_validate(payload["event"])
    except (KeyError, ValidationError) as exc:
        raise TaskError(f"Invalid AnomalyDetected event: {exc}") from exc
    d = event.detail
    if d.severity not in ("WARNING", "CRITICAL"):
        raise TaskError(f"Severity {d.severity} does not open incidents")

    incident_id = _new_id("INC")
    incident_sk = keys.incident_sk(now, incident_id)
    anomaly = event.model_dump(by_alias=True, mode="json")["detail"]
    base = {
        "incidentId": incident_id,
        "incidentSK": incident_sk,
        "machineId": d.machine_id,
        "operationId": d.operation_id,
        "zoneId": d.zone_id,
        "model": d.model,
        "severity": d.severity,
        "detectedAt": anomaly["detectedAt"],
        "createdAt": now,
        "anomaly": anomaly,
        "settings": settings,
        "roles": list(ROLES),
    }
    try:
        table.update_item(
            Key=keys.machine_profile(d.machine_id),
            UpdateExpression="SET openIncidentId = :id, openIncidentSK = :sk, openIncidentSeverity = :sev",
            ConditionExpression="attribute_exists(PK) AND attribute_not_exists(openIncidentId)",
            ExpressionAttributeValues={":id": incident_id, ":sk": incident_sk, ":sev": d.severity},
        )
        return {**base, "duplicate": False, "escalated": False}
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise

    profile = table.get_item(Key=keys.machine_profile(d.machine_id)).get("Item")
    if not profile:
        raise TaskError(f"Unknown machine {d.machine_id}")
    open_id, open_sk = profile["openIncidentId"], profile["openIncidentSK"]
    current = profile.get("openIncidentSeverity", "WARNING")
    escalated = SEVERITY_ORDER[d.severity] > SEVERITY_ORDER.get(current, 0)
    if escalated:
        _escalate(table, d.machine_id, d.operation_id, open_sk, d.severity, now)
    return {**base, "incidentId": open_id, "incidentSK": open_sk, "duplicate": True, "escalated": escalated}


def _escalate(table: Any, machine_id: str, op: str, incident_sk: str, severity: str, now: str) -> None:
    created_at = incident_sk.split("#")[1]
    try:
        table.update_item(
            Key=_incident_key(machine_id, incident_sk),
            UpdateExpression="SET severity = :sev, GSI1SK = :sk, timeline = list_append(timeline, :ev)",
            ConditionExpression="GSI1PK = :open",
            ExpressionAttributeValues={
                ":sev": severity,
                ":open": f"OP#{op}#INCIDENT#OPEN",
                ":sk": keys.incident_gsi1(op, "OPEN", severity, created_at)["GSI1SK"],
                ":ev": _timeline("ESCALATED", now, severity=severity),
            },
        )
    except Exception as exc:
        if not is_conditional_failure(exc):  # the incident row may not exist yet (still in EnrichContext)
            raise
    table.update_item(
        Key=keys.machine_profile(machine_id),
        UpdateExpression="SET openIncidentSeverity = :sev",
        ExpressionAttributeValues={":sev": severity},
    )


def release_claim(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Failure path: free the machine so a later anomaly can open a new incident."""
    inc = payload["incident"]
    release_claim_only(table, inc)
    error = payload.get("error") or {}
    try:
        table.update_item(
            Key=_incident_key(inc["machineId"], inc["incidentSK"]),
            UpdateExpression="SET #st = :failed, timeline = list_append(timeline, :ev)",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":ev": _timeline("FAILED", now, error=str(error.get("Error", ""))[:200]),
            },
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise
    return {"released": True}


# ----------------------------------------------------------------------------------------------- enrich
def _latest_reading(table: Any, machine_id: str) -> dict[str, Any] | None:
    res = table.query(
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(machine_id)) & Key("SK").begins_with("READING#"),
        ScanIndexForward=False,
        Limit=1,
    )
    return from_ddb(res["Items"][0]) if res["Items"] else None


def get_profile(table: Any, payload: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    inc = payload["incident"]
    item = table.get_item(Key=keys.machine_profile(inc["machineId"])).get("Item")
    if not item:
        raise TaskError(f"Unknown machine {inc['machineId']}")
    p = from_ddb(item)
    reading = _latest_reading(table, inc["machineId"]) or {}
    batch = (reading.get("context") or {}).get("fuelBatchId") or p.get("fuelBatchId")
    return {
        "machineId": inc["machineId"],
        "model": p.get("model"),
        "machineType": p.get("machineType"),
        "zoneId": p.get("zoneId"),
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "status": p.get("status"),
        "engineHours": p.get("engineHours"),
        "lastMaintenanceHours": p.get("lastMaintenanceHours"),
        "fuelBatchId": batch,
        "candidateFailureModes": catalog.get("failureModes", []),
    }


def get_history(table: Any, payload: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    pk = keys.machine_pk(payload["incident"]["machineId"])
    out = {}
    for name, prefix in (("incidents", "INCIDENT#"), ("maintenance", "MAINT#")):
        res = table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(prefix),
            ScanIndexForward=False,
            Limit=limit + 1,
        )
        rows = [strip_keys(i) for i in res["Items"] if i.get("incidentId") != payload["incident"]["incidentId"]]
        for r in rows:
            r.pop("taskToken", None)
            r.pop("anomaly", None)
        out[name] = rows[:limit]
    return out


def find_zone_correlation(table: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """GSI2: incidents of OTHER machines in the same zone within the window (P11)."""
    inc = payload["incident"]
    window = int(inc["settings"].get("zoneWindowMinutes", 120))
    since = minus(inc["createdAt"], minutes=window)
    res = table.query(
        IndexName="GSI2",
        KeyConditionExpression=Key("GSI2PK").eq(f"ZONE#{inc['operationId']}#{inc['zoneId']}")
        & Key("GSI2SK").gte(since),
    )
    machines: dict[str, dict[str, Any]] = {}
    for row in map(from_ddb, res["Items"]):
        mid = row["PK"].removeprefix("MACHINE#")
        if mid == inc["machineId"] or row.get("state") == "FAILED":
            continue
        machines[mid] = {
            "machineId": mid,
            "incidentId": row.get("incidentId"),
            "model": row.get("model"),
            "failureMode": row.get("failureMode"),
            "fuelBatchId": row.get("fuelBatchId"),
            "createdAt": row.get("createdAt"),
            "incidentSK": row["SK"],
            "state": row.get("state"),
        }
    return {"zoneId": inc["zoneId"], "windowMinutes": window, "machines": list(machines.values())}


# ----------------------------------------------------------------------------------------------- diagnosis
def rule_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback with the same shape as ai-diagnosis, so the flow never depends on the LLM."""
    inc, ctx = payload["incident"], payload.get("context", {})
    profile = ctx.get("profile", {})
    modes = profile.get("candidateFailureModes", [])
    signals = inc["anomaly"]["signals"]
    strong = {s["metric"] for s in signals if abs(s["zScore"]) >= 2} or {s["metric"] for s in signals}

    best = None
    for mode in modes:
        wanted = set(mode["metrics"])
        if wanted <= strong and (best is None or len(wanted) > len(best["metrics"])):
            best = mode
    if best is None:
        overlap = [(len(set(m["metrics"]) & strong), m) for m in modes]
        overlap = [o for o in overlap if o[0]]
        best = max(overlap, key=lambda o: o[0])[1] if overlap else None

    machine, sev = inc["machineId"], inc["severity"]
    zone = ctx.get("zone", {})
    detail = ", ".join(f"{s['metric']} {s['value']} (base {s['baseline']})" for s in signals[:3])
    label = best["label"] if best else "Anomalia sin patron conocido"
    stop = sev == "CRITICAL"
    hypothesis = zone_hypothesis(profile, zone, best["mode"] if best else None)
    messages = {
        "OPERATOR": f"{machine}: {'DETENER la maquina de forma segura' if stop else 'continuar con precaucion'}. "
        "Un tecnico fue asignado.",
        "TECHNICIAN": f"{machine} ({inc['zoneId']}): {label.lower()} probable. Senales: {detail}.",
        "MAINT_LEAD": f"Incidente {sev} en {machine}: {label.lower()} probable.",
        "SUPERVISOR": f"{machine} en {inc['zoneId']}: {label.lower()} probable ({sev}).",
        "MANAGER": f"{machine}: incidente {sev} en atencion.",
    }
    return {
        "schemaVersion": "1.0",
        "source": "RULES",
        "failureMode": best["mode"] if best else "UNKNOWN",
        "diagnosis": f"{label} probable: {detail}."[:400],
        "confidence": 0.6 if best else 0.3,
        "recommendedParts": best["parts"] if best else [],
        "requiredSpecialty": best["specialty"] if best else "MECHANICAL",
        "operatorAction": "STOP" if stop else "CONTINUE_WITH_CAUTION",
        "messages": messages,
        "zoneHypothesis": hypothesis,
    }


def zone_hypothesis(profile: dict[str, Any], zone: dict[str, Any], mode: str | None) -> str | None:
    others = zone.get("machines", [])
    if not others:
        return None
    group = [{"model": profile.get("model"), "fuelBatchId": profile.get("fuelBatchId"), "failureMode": mode}] + others
    total = len(group)
    commons = []
    for field, label in (("fuelBatchId", "lote de combustible"), ("failureMode", "modo de falla"), ("model", "modelo")):
        values = [g.get(field) for g in group if g.get(field)]
        if values and len(values) == total and len(set(values)) == 1:
            commons.append(f"{label} {values[0]}")
    if not commons:
        return f"{total} maquinas con incidentes en {zone.get('zoneId')} sin un factor comun evidente."
    return f"{total} maquinas en {zone.get('zoneId')} comparten {', '.join(commons)}: probable causa comun."


def normalize_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the AI response; fall back to rules if it is missing or invalid."""
    response = (payload.get("ai") or {}).get("response")
    if response is not None:
        try:
            ai = AiDiagnosisResponse.model_validate(response).model_dump(by_alias=True)
            known = {m["mode"] for m in payload.get("context", {}).get("profile", {}).get("candidateFailureModes", [])}
            if ai["failureMode"] in known | {"UNKNOWN"}:
                if not ai.get("zoneHypothesis"):
                    ai["zoneHypothesis"] = rule_diagnosis(payload)["zoneHypothesis"]
                return ai
        except ValidationError:
            pass
    return rule_diagnosis(payload)


# ----------------------------------------------------------------------------------------------- create
def create_incident(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    inc, ctx = payload["incident"], payload.get("context", {})
    diagnosis = normalize_diagnosis(payload)
    zone = ctx.get("zone", {"machines": []})
    profile = ctx.get("profile", {})
    min_machines = int(inc["settings"].get("zoneMinMachines", 3))
    zone_incident = len(zone.get("machines", [])) + 1 >= min_machines
    item = {
        **_incident_key(inc["machineId"], inc["incidentSK"]),
        **keys.incident_gsi1(inc["operationId"], "OPEN", inc["severity"], inc["createdAt"]),
        **keys.incident_gsi2(inc["operationId"], inc["zoneId"], inc["createdAt"], inc["machineId"]),
        "kind": "MACHINE",
        "incidentId": inc["incidentId"],
        "machineId": inc["machineId"],
        "operationId": inc["operationId"],
        "zoneId": inc["zoneId"],
        "model": inc["model"],
        "severity": inc["severity"],
        "state": "OPEN",
        "createdAt": inc["createdAt"],
        "detectedAt": inc["detectedAt"],
        "healthScore": inc["anomaly"]["healthScore"],
        "signals": inc["anomaly"]["signals"],
        "failureMode": diagnosis["failureMode"],
        "aiDiagnosis": diagnosis["diagnosis"],
        "aiSource": diagnosis["source"],
        "aiConfidence": diagnosis["confidence"],
        "operatorAction": diagnosis["operatorAction"],
        "requiredSpecialty": diagnosis["requiredSpecialty"],
        "fuelBatchId": profile.get("fuelBatchId"),
        "engineHoursAtDetection": profile.get("engineHours"),
        "correlatedIncidents": [m["incidentId"] for m in zone.get("machines", []) if m.get("incidentId")],
        "zoneHypothesis": diagnosis.get("zoneHypothesis"),
        "timeline": _timeline("DETECTED", inc["detectedAt"], severity=inc["severity"])
        + _timeline("DIAGNOSED", now, source=diagnosis["source"], failureMode=diagnosis["failureMode"]),
    }
    item = {k: v for k, v in item.items() if v is not None}
    try:
        table.put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")
    except Exception as exc:
        if not is_conditional_failure(exc):  # retry after a successful write: keep the first one
            raise
    return {
        "zoneIncident": zone_incident,
        "diagnosis": diagnosis,
        "correlatedIncidents": item.get("correlatedIncidents", []),
    }


def create_zone_incident(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """One open zone incident per zone and window; later machines join it (P11)."""
    inc, created = payload["incident"], payload["created"]
    op, zone_id = inc["operationId"], inc["zoneId"]
    zone = payload.get("context", {}).get("zone", {})
    others = zone.get("machines", [])
    open_others = [m for m in others if m.get("state") != "CLOSED" and m.get("incidentSK")]
    all_ids = {inc["incidentId"], *(m["incidentId"] for m in others if m.get("incidentId"))}
    members = {inc["incidentId"], *(m["incidentId"] for m in open_others)}
    machines = {inc["machineId"], *(m["machineId"] for m in others)}
    to_link = [inc, *open_others]
    window = int(inc["settings"].get("zoneWindowMinutes", 120))
    zinc_id = _new_id("ZINC")
    zinc_sk = f"ZINC#{now}#{zinc_id}"
    lock = {"PK": f"OP#{op}", "SK": f"ZONE_INCIDENT_LOCK#{zone_id}"}
    try:
        table.put_item(
            Item={**lock, "zoneIncidentId": zinc_id, "zoneIncidentSK": zinc_sk, "openedAt": now},
            ConditionExpression="attribute_not_exists(PK) OR openedAt < :cutoff",
            ExpressionAttributeValues={":cutoff": minus(now, minutes=window)},
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise
        existing = from_ddb(table.get_item(Key=lock)["Item"])
        table.update_item(
            Key={"PK": f"OP#{op}", "SK": existing["zoneIncidentSK"]},
            UpdateExpression="ADD incidentIds :all, machineIds :m, openMembers :inc",
            ExpressionAttributeValues={":all": all_ids, ":inc": members, ":m": machines},
        )
        for member in to_link:
            _link_incident(table, member, existing["zoneIncidentId"], existing["zoneIncidentSK"])
        return {"created": False, "zoneIncidentId": existing["zoneIncidentId"]}

    diagnosis = created["diagnosis"]
    table.put_item(
        Item=to_ddb(
            {
                "PK": f"OP#{op}",
                "SK": zinc_sk,
                **keys.incident_gsi1(op, "OPEN", "CRITICAL", now),
                "kind": "ZONE",
                "zoneIncidentId": zinc_id,
                "operationId": op,
                "zoneId": zone_id,
                "severity": "CRITICAL",
                "state": "OPEN",
                "createdAt": now,
                "incidentIds": all_ids,
                "openMembers": members,
                "machineIds": machines,
                "hypothesis": diagnosis.get("zoneHypothesis")
                or f"{len(machines)} maquinas con incidentes en {zone_id}",
                "timeline": _timeline("DETECTED", now, machines=len(machines)),
            }
        )
    )
    for member in to_link:
        _link_incident(table, member, zinc_id, zinc_sk)
    return {
        "created": True,
        "zoneIncidentId": zinc_id,
        "machines": sorted(machines),
        "hypothesis": diagnosis.get("zoneHypothesis"),
    }


def _link_incident(table: Any, inc: dict[str, Any], zinc_id: str, zinc_sk: str) -> None:
    try:
        table.update_item(
            Key=_incident_key(inc["machineId"], inc["incidentSK"]),
            UpdateExpression="SET zoneIncidentId = :z, zoneIncidentSK = :zsk",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":z": zinc_id, ":zsk": zinc_sk},
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise


# ----------------------------------------------------------------------------------------------- parts
def reserve_parts(table: Any, payload: dict[str, Any], catalog: dict[str, Any], now: str) -> dict[str, Any]:
    """Atomic reservation, nearest warehouse first (P3). Idempotent per incident via reservations.{incidentId}.

    The guide's condition `qty - reserved >= :n` is not valid DynamoDB syntax (no arithmetic in conditions),
    so stock keeps an explicit `available` counter: available = qty - reserved.
    """
    inc = payload["incident"]
    profile = payload.get("context", {}).get("profile", {})
    wanted = payload["created"]["diagnosis"].get("recommendedParts", [])
    warehouses = catalog.get("warehouses", [])
    if profile.get("lat") is not None:
        warehouses = sorted(warehouses, key=lambda w: haversine_km(profile["lat"], profile["lon"], w["lat"], w["lon"]))
    reserved, missing = [], []
    for part in wanted:
        done = None
        for wh in warehouses:
            stock_key = {"PK": f"PART#{part['partId']}", "SK": f"STOCK#{wh['warehouseId']}"}
            try:
                table.update_item(
                    Key=stock_key,
                    UpdateExpression="SET available = available - :n, reserved = reserved + :n, "
                    "reservations.#inc = :n, updatedAt = :now",
                    ConditionExpression="available >= :n AND attribute_not_exists(reservations.#inc)",
                    ExpressionAttributeNames={"#inc": inc["incidentId"]},
                    ExpressionAttributeValues={":n": part["qty"], ":now": now},
                )
                done = {"partId": part["partId"], "warehouseId": wh["warehouseId"], "qty": part["qty"]}
            except Exception as exc:
                if not is_conditional_failure(exc):
                    raise
                row = table.get_item(Key=stock_key).get("Item") or {}
                if inc["incidentId"] in (row.get("reservations") or {}):  # retry of a reservation that succeeded
                    done = {"partId": part["partId"], "warehouseId": wh["warehouseId"], "qty": part["qty"]}
            if done:
                reserved.append(done)
                break
        if not done:
            missing.append(part)
    events = _timeline("PARTS_RESERVED", now, parts=len(reserved)) if reserved else []
    if missing:
        events += _timeline("PARTS_MISSING", now, parts=[m["partId"] for m in missing])
    table.update_item(
        Key=_incident_key(inc["machineId"], inc["incidentSK"]),
        UpdateExpression="SET partsReserved = :r, partsMissing = :m, timeline = list_append(timeline, :ev)",
        ExpressionAttributeValues={":r": reserved, ":m": missing, ":ev": events},
    )
    return {"reserved": reserved, "missing": missing, "hasMissing": bool(missing)}


def _settle_parts(
    table: Any, incident_id: str, reserved: list[dict[str, Any]], used: list[dict[str, Any]], now: str
) -> list[dict[str, Any]]:
    """On close: consume what was used, return the rest to `available`. Idempotent (removes the reservation)."""
    used_qty: dict[str, int] = {}
    for u in used:
        used_qty[u["partId"]] = used_qty.get(u["partId"], 0) + int(u["qty"])
    consumed = []
    for r in reserved:
        take = min(used_qty.get(r["partId"], 0), int(r["qty"]))
        used_qty[r["partId"]] = used_qty.get(r["partId"], 0) - take
        try:
            table.update_item(
                Key={"PK": f"PART#{r['partId']}", "SK": f"STOCK#{r['warehouseId']}"},
                UpdateExpression="SET qty = qty - :take, reserved = reserved - :res, available = available + :back, "
                "updatedAt = :now REMOVE reservations.#inc",
                ConditionExpression="attribute_exists(reservations.#inc)",
                ExpressionAttributeNames={"#inc": incident_id},
                ExpressionAttributeValues={
                    ":take": take,
                    ":res": int(r["qty"]),
                    ":back": int(r["qty"]) - take,
                    ":now": now,
                },
            )
        except Exception as exc:
            if not is_conditional_failure(exc):
                raise
        if take:
            consumed.append({**r, "qty": take})
    return consumed


# ----------------------------------------------------------------------------------------------- waits
def store_task_token(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Called by .waitForTaskToken states; POST /ack resumes the execution with SendTaskSuccess."""
    inc = payload["incident"]
    table.update_item(
        Key=_incident_key(inc["machineId"], inc["incidentSK"]),
        UpdateExpression="SET taskToken = :t, pendingStage = :s, pendingSince = :now",
        ExpressionAttributeValues={":t": payload["taskToken"], ":s": payload["stage"], ":now": now},
    )
    return {"stored": payload["stage"]}


# ----------------------------------------------------------------------------------------------- close
def _recurrence(table: Any, inc: dict[str, Any], failure_mode: str | None, days: int) -> str | None:
    if not failure_mode or failure_mode == "UNKNOWN":
        return None
    since = minus(inc["createdAt"], days=days)
    res = table.query(
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(inc["machineId"]))
        & Key("SK").between(f"INCIDENT#{since}", f"INCIDENT#{inc['createdAt']}"),
        ScanIndexForward=False,
    )
    for row in res["Items"]:
        if (
            row.get("incidentId") != inc["incidentId"]
            and row.get("state") == "CLOSED"
            and row.get("failureMode") == failure_mode
        ):
            return row["incidentId"]
    return None


def close_incident(table: Any, payload: dict[str, Any], now: str, store: Any = None) -> dict[str, Any]:
    inc = payload["incident"]
    key = _incident_key(inc["machineId"], inc["incidentSK"])
    row = table.get_item(Key=key).get("Item")
    if not row:
        raise TaskError(f"Incident {inc['incidentId']} not found")
    row = from_ddb(row)
    if row.get("state") == "CLOSED":
        return {"incidentId": inc["incidentId"], "alreadyClosed": True, "downtimeMinutes": row.get("downtimeMinutes")}

    resolution = payload.get("resolution") or {}
    resolved_at = resolution.get("at") or now
    downtime = max(0, round((parse_iso(resolved_at) - parse_iso(inc["detectedAt"])).total_seconds() / 60))
    reserved = row.get("partsReserved", [])
    used_input = resolution.get("partsUsed")
    used = _settle_parts(table, inc["incidentId"], reserved, reserved if used_input is None else used_input, now)
    recurrence = _recurrence(table, inc, row.get("failureMode"), int(inc["settings"].get("recurrenceWindowDays", 30)))
    tech = row.get("assignedTech")
    if tech:
        techs.release(table, tech, inc["operationId"], inc["incidentId"])

    maint_id = _new_id("MNT")
    table.put_item(
        Item=to_ddb(
            {
                "PK": keys.machine_pk(inc["machineId"]),
                "SK": keys.maintenance_sk(resolved_at[:10], maint_id),
                "maintenanceId": maint_id,
                "type": "CORRECTIVE",
                "incidentId": inc["incidentId"],
                "machineId": inc["machineId"],
                "date": resolved_at[:10],
                "at": resolved_at,
                "techId": tech,
                "failureMode": row.get("failureMode"),
                "partsUsed": used,
                "downtimeMinutes": downtime,
                "notes": resolution.get("notes"),
            }
        )
    )

    updates = [
        "#st = :closed",
        "GSI1PK = :gsi1pk",
        "closedAt = :now",
        "downtimeMinutes = :dt",
        "partsUsed = :used",
        "timeline = list_append(timeline, :ev)",
        "maintenanceId = :mnt",
    ]
    values: dict[str, Any] = {
        ":closed": "CLOSED",
        ":gsi1pk": f"OP#{inc['operationId']}#INCIDENT#CLOSED",
        ":now": now,
        ":dt": downtime,
        ":used": used,
        ":mnt": maint_id,
        ":ev": _timeline("CLOSED", now, downtimeMinutes=downtime),
    }
    if recurrence:
        updates.append("recurrenceOf = :rec")
        values[":rec"] = recurrence
    table.update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(updates) + " REMOVE taskToken, pendingStage",
        ExpressionAttributeNames={"#st": "state"},
        ExpressionAttributeValues=to_ddb(values),
    )

    release_claim_only(table, inc)

    if row.get("zoneIncidentSK"):
        _leave_zone_incident(table, inc, row["zoneIncidentSK"], now)
    if store is not None:
        clear_simulated_faults(store, inc["machineId"])
    return {
        "incidentId": inc["incidentId"],
        "downtimeMinutes": downtime,
        "partsUsed": used,
        "recurrenceOf": recurrence,
        "maintenanceId": maint_id,
        "techId": tech,
    }


def release_claim_only(table: Any, inc: dict[str, Any]) -> None:
    try:
        table.update_item(
            Key=keys.machine_profile(inc["machineId"]),
            UpdateExpression="REMOVE openIncidentId, openIncidentSK, openIncidentSeverity",
            ConditionExpression="openIncidentId = :id",
            ExpressionAttributeValues={":id": inc["incidentId"]},
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise


def _leave_zone_incident(table: Any, inc: dict[str, Any], zinc_sk: str, now: str) -> None:
    zkey = {"PK": f"OP#{inc['operationId']}", "SK": zinc_sk}
    row = table.update_item(
        Key=zkey,
        UpdateExpression="DELETE openMembers :inc",
        ExpressionAttributeValues={":inc": {inc["incidentId"]}},
        ReturnValues="ALL_NEW",
    )
    if not row.get("Attributes", {}).get("openMembers"):
        try:
            table.update_item(
                Key=zkey,
                UpdateExpression="SET #st = :closed, GSI1PK = :pk, closedAt = :now, "
                "timeline = list_append(timeline, :ev)",
                ConditionExpression="#st = :open",
                ExpressionAttributeNames={"#st": "state"},
                ExpressionAttributeValues={
                    ":closed": "CLOSED",
                    ":open": "OPEN",
                    ":now": now,
                    ":pk": f"OP#{inc['operationId']}#INCIDENT#CLOSED",
                    ":ev": _timeline("CLOSED", now),
                },
            )
            table.delete_item(
                Key={"PK": f"OP#{inc['operationId']}", "SK": f"ZONE_INCIDENT_LOCK#{inc['zoneId']}"},
                ConditionExpression="zoneIncidentSK = :zsk",
                ExpressionAttributeValues={":zsk": zinc_sk},
            )
        except Exception as exc:
            if not is_conditional_failure(exc):
                raise


def clear_simulated_faults(store: Any, machine_id: str) -> None:
    from af_shared import config

    current = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
    kept = [f for f in current if f.get("machineId") != machine_id]
    if len(kept) != len(current):
        store.put_json(config.SIM_FAULTS, {"faults": kept})


def update_machine_state(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """After a corrective repair the usage counter for preventive maintenance restarts (P2)."""
    inc = payload["incident"]
    res = table.update_item(
        Key=keys.machine_profile(inc["machineId"]),
        UpdateExpression="SET lastMaintenanceHours = if_not_exists(engineHours, lastMaintenanceHours), "
        "lastMaintenanceAt = :now",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={":now": now},
        ReturnValues="ALL_NEW",
    )
    attrs = from_ddb(res.get("Attributes", {}))
    return {"machineId": inc["machineId"], "lastMaintenanceHours": attrs.get("lastMaintenanceHours")}
