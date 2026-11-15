"""Incident lifecycle tasks for the af-dev-incident Step Function (P3, P10, P11). Owner: Rol 2.

Every function is (table, payload, now) -> JSON-able dict, so the Step Function state stays small and the
logic is testable with af_shared.fakes.FakeTable. Writes are conditional so Step Functions retries are safe.

Incident items follow the Incident shape of frontend/src/api/types.ts. `timeline` only carries the events the
web shows (DETECTED, ASSIGNED, ARRIVED, RESOLVED, ESCALATED); everything else goes to `activity`.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from af_shared import AnomalyDetected, DiagnosisResult, keys, techs
from af_shared.ddb import from_ddb, is_conditional_failure, minus, parse_iso, to_ddb
from af_shared.fleet import FleetConfig
from boto3.dynamodb.conditions import Key
from pydantic import ValidationError

ROLES = ("OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER")
SEVERITY_ORDER = {"OK": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
TIMELINE_EVENTS = {"DETECTED", "ASSIGNED", "ARRIVED", "RESOLVED", "ESCALATED"}
REPAIR_MINUTES = {
    "BEARING": 120,
    "COOLING_SYSTEM": 60,
    "HYDRAULIC_SYSTEM": 90,
    "FUEL_SYSTEM": 60,
    "ENGINE": 120,
    "DRIVETRAIN": 150,
    "SENSOR": 30,
    "UNKNOWN": 90,
}


class TaskError(Exception):
    """Raised for unrecoverable input problems; the Step Function catches it and releases the claim."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(2)}"


def _operation_settings(table: Any, operation_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Shared settings (SSM) + optional per-operation overrides kept in DynamoDB, OP#{op} / INCIDENT_SETTINGS:
    {"settings": {...}, "zoneWarehouses": {"ZE": ["WH-E0001"]}}. Lets an isolated operation (lab rehearsal)
    change timeouts and which warehouses serve its zones without touching shared parameters."""
    row = table.get_item(Key={"PK": f"OP#{operation_id}", "SK": "INCIDENT_SETTINGS"}).get("Item")
    if not row:
        return settings
    row = from_ddb(row)
    merged = {**settings, **(row.get("settings") or {})}
    if row.get("zoneWarehouses"):
        merged["zoneWarehouses"] = row["zoneWarehouses"]
    return merged


def _id_for_execution(execution_id: str) -> str:
    return f"INC-{hashlib.sha256(execution_id.encode()).hexdigest()[:4]}"


def _event(event: str, at: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"at": at, "event": event, **{k: v for k, v in extra.items() if v is not None}}]


def _incident_key(machine_id: str, incident_sk: str) -> dict[str, str]:
    return {"PK": keys.machine_pk(machine_id), "SK": incident_sk}


def _fleet(store: Any) -> FleetConfig:
    """Fleet catalog: AF_INCIDENT_CATALOG (Rol 1: /af/incident/fleet-catalog) or the simulator's fleet-config."""
    import os

    from af_shared import config

    return FleetConfig.model_validate(store.get_json(os.environ.get("AF_INCIDENT_CATALOG") or config.FLEET_CONFIG))


def _ignore_conditional(fn: Any, **kwargs: Any) -> bool:
    try:
        fn(**kwargs)
        return True
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise
        return False


# ----------------------------------------------------------------------------------------------- claim
def claim(table: Any, payload: dict[str, Any], now: str, settings: dict[str, Any]) -> dict[str, Any]:
    """ValidateEvent: validate AnomalyDetected and atomically claim 'one open incident per machine'.

    The health-evaluator emits one event per escalation; if a machine already has an open incident the new
    execution only escalates its severity (WARNING -> CRITICAL) and ends.
    """
    try:
        event = AnomalyDetected.model_validate(payload["event"])
    except (KeyError, ValidationError) as exc:
        raise TaskError(f"Invalid AnomalyDetected event: {exc}") from exc
    d = event.detail
    if d.severity not in ("WARNING", "CRITICAL"):
        raise TaskError(f"Severity {d.severity} does not open incidents")

    settings = _operation_settings(table, d.operation_id, settings)
    execution = payload.get("executionId")  # $$.Execution.Id: stable across Lambda/Step Functions retries
    incident_id = _id_for_execution(execution) if execution else _new_id("INC")
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
    if _ignore_conditional(
        table.update_item,
        Key=keys.machine_profile(d.machine_id),
        UpdateExpression="SET openIncidentId = :id, openIncidentSK = :sk, openIncidentSeverity = :sev, "
        "openIncidentExecution = :ex, lastIncidentDetectedAt = :det",
        # lastIncidentDetectedAt: an alert re-published by the health-evaluator outbox (pendingAlert) after its
        # incident was already handled must not open a second incident.
        ConditionExpression="attribute_exists(PK) AND attribute_not_exists(openIncidentId) "
        "AND (attribute_not_exists(lastIncidentDetectedAt) OR lastIncidentDetectedAt < :det)",
        ExpressionAttributeValues={
            ":id": incident_id,
            ":sk": incident_sk,
            ":sev": d.severity,
            ":ex": execution or "-",
            ":det": anomaly["detectedAt"],
        },
    ):
        return {**base, "duplicate": False, "escalated": False}

    profile = table.get_item(Key=keys.machine_profile(d.machine_id), ConsistentRead=True).get("Item")
    if not profile:
        raise TaskError(f"Unknown machine {d.machine_id}")
    if "openIncidentId" not in profile:  # replay of an alert that already had its incident
        return {**base, "duplicate": True, "escalated": False, "replayed": True}
    open_id, open_sk = profile["openIncidentId"], profile["openIncidentSK"]
    if execution and profile.get("openIncidentExecution") == execution:
        # Our own claim whose response was lost (retry): keep going with the claimed incident, not a duplicate.
        created_at = open_sk.split("#")[1]
        return {
            **base,
            "incidentId": open_id,
            "incidentSK": open_sk,
            "createdAt": created_at,
            "duplicate": False,
            "escalated": False,
        }
    escalated = SEVERITY_ORDER[d.severity] > SEVERITY_ORDER.get(profile.get("openIncidentSeverity", "WARNING"), 0)
    if escalated:
        _escalate(table, d.machine_id, d.operation_id, open_sk, d.severity, d.health_score, now)
    return {**base, "incidentId": open_id, "incidentSK": open_sk, "duplicate": True, "escalated": escalated}


def _escalate(table: Any, machine_id: str, op: str, incident_sk: str, severity: str, health: float, now: str) -> None:
    created_at = incident_sk.split("#")[1]
    _ignore_conditional(  # the incident row may not exist yet (first execution still in EnrichContext)
        table.update_item,
        Key=_incident_key(machine_id, incident_sk),
        UpdateExpression="SET severity = :sev, healthScore = :h, GSI1SK = :sk, aiDiagnosis.stopMachine = :stop, "
        "timeline = list_append(timeline, :ev)",
        ConditionExpression="GSI1PK = :open",
        ExpressionAttributeValues=to_ddb(
            {
                ":sev": severity,
                ":h": health,
                ":open": f"OP#{op}#INCIDENT#OPEN",
                ":stop": severity == "CRITICAL",
                ":sk": keys.incident_gsi1(op, "OPEN", severity, created_at)["GSI1SK"],
                ":ev": _event("ESCALATED", now),
            }
        ),
    )
    table.update_item(
        Key=keys.machine_profile(machine_id),
        UpdateExpression="SET openIncidentSeverity = :sev",
        ExpressionAttributeValues={":sev": severity},
    )


def release_claim_only(table: Any, inc: dict[str, Any]) -> None:
    _ignore_conditional(
        table.update_item,
        Key=keys.machine_profile(inc["machineId"]),
        UpdateExpression="REMOVE openIncidentId, openIncidentSK, openIncidentSeverity, openIncidentExecution",
        ConditionExpression="openIncidentId = :id",
        ExpressionAttributeValues={":id": inc["incidentId"]},
    )


def release_claim(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Failure path: free the machine so a later anomaly can open a new incident."""
    inc = payload["incident"]
    release_claim_only(table, inc)
    error = payload.get("error") or {}
    _ignore_conditional(
        table.update_item,
        Key=_incident_key(inc["machineId"], inc["incidentSK"]),
        UpdateExpression="SET flowFailed = :t, GSI1PK = :closed, activity = list_append(activity, :ev) "
        "REMOVE taskToken, pendingStage, waitGeneration",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={
            ":t": True,
            ":closed": f"OP#{inc['operationId']}#INCIDENT#CLOSED",
            ":ev": _event("FAILED", now, error=str(error.get("Error", ""))[:200]),
        },
    )
    return {"released": True}


# ----------------------------------------------------------------------------------------------- enrich
def _latest_reading(table: Any, machine_id: str) -> dict[str, Any] | None:
    res = table.query(
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(machine_id)) & Key("SK").begins_with("READING#"),
        ScanIndexForward=False,
        Limit=1,
    )
    return from_ddb(res["Items"][0]) if res["Items"] else None


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def get_profile(table: Any, payload: dict[str, Any]) -> dict[str, Any]:
    inc = payload["incident"]
    item = table.get_item(Key=keys.machine_profile(inc["machineId"])).get("Item")
    if not item:
        raise TaskError(f"Unknown machine {inc['machineId']}")
    p = from_ddb(item)
    reading_ctx = (_latest_reading(table, inc["machineId"]) or {}).get("context") or {}
    batch = reading_ctx.get("fuelBatchId") or p.get("fuelBatchId")
    return {
        "machineId": inc["machineId"],
        "model": p.get("model"),
        "zoneId": p.get("zoneId"),
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "fuelBatchId": batch,
        # contract-ready blocks for the ai-diagnosis request (additionalProperties: false)
        "aiMachine": _clean(
            {
                "status": p.get("status"),
                "engineHours": p.get("engineHours"),
                "lastMaintenanceHours": p.get("lastMaintenanceHours"),
            }
        ),
        "aiContext": _clean({"fuelBatchId": batch, "ambientTempC": reading_ctx.get("ambientTempC")}),
    }


def get_history(table: Any, payload: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    pk = keys.machine_pk(payload["incident"]["machineId"])
    entries = []
    for prefix in ("INCIDENT#", "MAINT#"):
        res = table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(prefix),
            ScanIndexForward=False,
            Limit=limit + 1,
        )
        for row in map(from_ddb, res["Items"]):
            if row.get("incidentId") == payload["incident"]["incidentId"]:
                continue
            if prefix == "INCIDENT#":
                diag = row.get("aiDiagnosis") or {}
                entries.append(
                    _clean(
                        {
                            "type": "INCIDENT",
                            "at": row.get("createdAt"),
                            "summary": f"{row.get('severity')} {diag.get('component', '')}: "
                            f"{diag.get('probableCause', '')}"[:300],
                            "partsReplaced": [p["partId"] for p in row.get("partsUsed", [])] or None,
                        }
                    )
                )
            else:
                entries.append(
                    _clean(
                        {
                            "type": "MAINTENANCE",
                            "at": row.get("date"),
                            "summary": (row.get("notes") or row.get("type", ""))[:300],
                            "partsReplaced": row.get("partsReplaced") or None,
                        }
                    )
                )
    entries = [e for e in entries if e.get("at")]
    entries.sort(key=lambda e: e["at"], reverse=True)
    return {"aiHistory": entries[:10]}


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
        if mid == inc["machineId"] or row.get("flowFailed"):
            continue
        signals = sorted(row.get("signals", []), key=lambda s: -abs(s.get("zScore", 0)))
        machines[mid] = {
            "machineId": mid,
            "incidentId": row.get("incidentId"),
            "incidentSK": row["SK"],
            "state": row.get("state"),
            "model": row.get("model"),
            "ai": _clean(
                {
                    "machineId": mid,
                    "model": row.get("model"),
                    "detectedAt": row.get("detectedAt"),
                    "severity": row.get("severity"),
                    "topMetric": signals[0]["metric"] if signals else None,
                    "fuelBatchId": row.get("fuelBatchId"),
                }
            ),
        }
    others = list(machines.values())
    return {
        "zoneId": inc["zoneId"],
        "windowMinutes": window,
        "machines": others,
        "aiZoneCorrelation": [m["ai"] for m in others],
    }


# ----------------------------------------------------------------------------------------------- diagnosis
def _component(strong: set[str], signals: list[dict[str, Any]]) -> str:
    if {"temperatureC", "vibrationMmS"} <= strong:
        return "BEARING"
    if "pressureBar" in strong:
        return "HYDRAULIC_SYSTEM"
    if "fuelRateLph" in strong:
        return "FUEL_SYSTEM"
    if "temperatureC" in strong:
        return "COOLING_SYSTEM"
    if "vibrationMmS" in strong:
        return "DRIVETRAIN"
    return "ENGINE" if signals else "UNKNOWN"


CAUSES = {
    "BEARING": (
        "Probable desgaste de rodamiento: temperatura y vibración suben juntas.",
        "Detener, inspeccionar rodamientos y lubricación; reemplazar si hay holgura.",
    ),
    "HYDRAULIC_SYSTEM": (
        "Probable fuga hidráulica: la presión cae por debajo de lo normal.",
        "Revisar mangueras, sellos y nivel de aceite; cambiar filtro hidráulico.",
    ),
    "FUEL_SYSTEM": (
        "Consumo de combustible anormal: posible combustible contaminado o inyectores.",
        "Drenar y cambiar filtro de combustible; tomar muestra del lote.",
    ),
    "COOLING_SYSTEM": (
        "Probable falla de refrigeración: la temperatura sube sin otra causa mecánica.",
        "Revisar refrigerante, radiador y ventilador.",
    ),
    "DRIVETRAIN": ("Vibración anormal en el tren de potencia.", "Inspeccionar transmisión y ejes."),
    "ENGINE": ("Anomalía de motor sin patrón claro.", "Inspección general del motor."),
    "UNKNOWN": ("Anomalía sin patrón conocido.", "Inspección general."),
}


def rule_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Fallback with the ai-diagnosis response shape (contracts/ai-diagnosis-response.schema.json).

    Only used if the af-dev-ai-diagnosis Lambda itself fails (it already has its own rules for Bedrock errors).
    """
    inc, ctx = payload["incident"], payload.get("context", {})
    signals = inc["anomaly"]["signals"]
    strong = {s["metric"] for s in signals if abs(s["zScore"]) >= 2} or {s["metric"] for s in signals}
    component = _component(strong, signals)
    cause, action = CAUSES[component]
    stop = inc["severity"] == "CRITICAL"
    zone = ctx.get("zone", {})
    others = zone.get("aiZoneCorrelation", [])
    zone_analysis = None
    if len(others) + 1 >= int(inc["settings"].get("zoneMinMachines", 3)):
        batch = (ctx.get("profile") or {}).get("fuelBatchId")
        batches = {o.get("fuelBatchId") for o in others} | {batch}
        common = (
            f"Posible lote de combustible contaminado {batch}."
            if batch and len(batches) == 1
            else f"{len(others) + 1} máquinas con incidentes en {inc['zoneId']}: revisar condiciones de la zona."
        )
        zone_analysis = {
            "isZoneIncident": True,
            "commonCause": common,
            "evidence": [f"{len(others) + 1} máquinas con incidentes en {inc['zoneId']} en las últimas 2 h"],
        }
    where = f"{inc['machineId']} ({inc['zoneId']})"
    zone_note = f" Incidente de zona: {zone_analysis['commonCause']}" if zone_analysis else ""
    minutes = REPAIR_MINUTES[component]
    return {
        "schemaVersion": "1.0",
        "source": "RULES",
        "modelId": None,
        "diagnosis": {
            "probableCause": cause,
            "component": component,
            "confidence": "MEDIUM" if component != "UNKNOWN" else "LOW",
            "recommendedAction": action,
            "stopMachine": stop,
            "estimatedRepairMinutes": minutes,
        },
        "zoneAnalysis": zone_analysis,
        "messages": {
            "OPERATOR": f"Detén {inc['machineId']} de forma segura y espera al técnico."
            if stop
            else f"Continúa con {inc['machineId']} reduciendo la carga; un técnico va en camino.",
            "TECHNICIAN": f"{where}: {cause} Acción: {action}",
            "MAINT_LEAD": f"Incidente {inc['severity']} en {where}. Componente {component}. "
            f"{minutes} min estimados.{zone_note}",
            "SUPERVISOR": f"{inc['machineId']} {'detenida' if stop else 'operando con restricción'} "
            f"en {inc['zoneId']}.{zone_note}",
            "MANAGER": f"{inc['operationId']}: incidente {inc['severity']} en {inc['machineId']}, "
            f"~{minutes} min de inactividad.{zone_note}",
        },
    }


def normalize_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the ai-diagnosis answer against the contract; fall back to rules if missing or invalid."""
    response = (payload.get("ai") or {}).get("response")
    if response is not None:
        try:
            return DiagnosisResult.model_validate(response).model_dump(by_alias=True, mode="json")
        except ValidationError:
            pass
    return rule_diagnosis(payload)


def web_diagnosis(result: dict[str, Any]) -> dict[str, Any]:
    """AiDiagnosis shape of frontend/src/api/types.ts."""
    d = result["diagnosis"]
    out = {
        "source": result["source"],
        **{
            k: d[k]
            for k in (
                "probableCause",
                "component",
                "confidence",
                "recommendedAction",
                "stopMachine",
                "estimatedRepairMinutes",
            )
        },
    }
    if result.get("zoneAnalysis"):
        out["zoneCause"] = result["zoneAnalysis"]["commonCause"]
    return out


# ----------------------------------------------------------------------------------------------- create
def create_incident(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    inc, ctx = payload["incident"], payload.get("context", {})
    result = normalize_diagnosis(payload)
    zone = ctx.get("zone", {"machines": []})
    profile = ctx.get("profile", {})
    min_machines = int(inc["settings"].get("zoneMinMachines", 3))
    zone_incident = (
        bool((result.get("zoneAnalysis") or {}).get("isZoneIncident"))
        or len(zone.get("machines", [])) + 1 >= min_machines
    )
    item = {
        **_incident_key(inc["machineId"], inc["incidentSK"]),
        **keys.incident_gsi1(inc["operationId"], "OPEN", inc["severity"], inc["createdAt"]),
        **keys.incident_gsi2(inc["operationId"], inc["zoneId"], inc["createdAt"], inc["machineId"]),
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
        "aiDiagnosis": web_diagnosis(result),
        "messages": result["messages"],
        "aiModelId": result.get("modelId"),
        "fuelBatchId": profile.get("fuelBatchId"),
        "engineHoursAtDetection": (profile.get("aiMachine") or {}).get("engineHours"),
        "partsReserved": [],
        "correlatedIncidents": [m["incidentId"] for m in zone.get("machines", []) if m.get("incidentId")],
        "timeline": _event("DETECTED", inc["detectedAt"]),
        "activity": _event("DIAGNOSED", now, source=result["source"], component=result["diagnosis"]["component"]),
    }
    _ignore_conditional(table.put_item, Item=to_ddb(_clean(item)), ConditionExpression="attribute_not_exists(PK)")
    return {"zoneIncident": zone_incident, "diagnosis": result, "correlatedIncidents": item["correlatedIncidents"]}


def create_zone_incident(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """One zone incident per zone and window; later machines join it (P11). Members get zoneIncidentId and the
    common cause in aiDiagnosis.zoneCause, which is what the web shows."""
    inc, created = payload["incident"], payload["created"]
    op, zone_id = inc["operationId"], inc["zoneId"]
    zone = payload.get("context", {}).get("zone", {})
    others = zone.get("machines", [])
    open_others = [m for m in others if m.get("state") not in ("RESOLVED", "CLOSED") and m.get("incidentSK")]
    all_ids = {inc["incidentId"], *(m["incidentId"] for m in others if m.get("incidentId"))}
    members = {inc["incidentId"], *(m["incidentId"] for m in open_others)}
    machines = {inc["machineId"], *(m["machineId"] for m in others)}
    to_link = [inc, *open_others]
    analysis = created["diagnosis"].get("zoneAnalysis") or {}
    cause = analysis.get("commonCause") or f"{len(machines)} máquinas con incidentes en {zone_id}."
    window = int(inc["settings"].get("zoneWindowMinutes", 120))
    lock = {"PK": f"OP#{op}", "SK": f"ZONE_INCIDENT_LOCK#{zone_id}"}
    zinc_id = _new_id("ZINC")
    zinc_sk = f"ZINC#{now}#{zinc_id}"
    created_now = _ignore_conditional(
        table.put_item,
        Item={**lock, "zoneIncidentId": zinc_id, "zoneIncidentSK": zinc_sk, "openedAt": now},
        ConditionExpression="attribute_not_exists(PK) OR openedAt < :cutoff",
        ExpressionAttributeValues={":cutoff": minus(now, minutes=window)},
    )
    if created_now:
        table.put_item(
            Item=to_ddb(
                {
                    "PK": f"OP#{op}",
                    "SK": zinc_sk,
                    "zoneIncidentId": zinc_id,
                    "operationId": op,
                    "zoneId": zone_id,
                    "state": "OPEN",
                    "createdAt": now,
                    "incidentIds": all_ids,
                    "openMembers": members,
                    "machineIds": machines,
                    "commonCause": cause,
                    "evidence": analysis.get("evidence", []),
                }
            )
        )
    else:
        existing = from_ddb(table.get_item(Key=lock)["Item"])
        zinc_id, zinc_sk = existing["zoneIncidentId"], existing["zoneIncidentSK"]
        table.update_item(
            Key={"PK": f"OP#{op}", "SK": zinc_sk},
            UpdateExpression="ADD incidentIds :all, machineIds :m, openMembers :inc",
            ExpressionAttributeValues={":all": all_ids, ":inc": members, ":m": machines},
        )
    for member in to_link:
        _ignore_conditional(
            table.update_item,
            Key=_incident_key(member["machineId"], member["incidentSK"]),
            UpdateExpression="SET zoneIncidentId = :z, zoneIncidentSK = :zsk, aiDiagnosis.zoneCause = :c, "
            "correlatedIncidents = :corr",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={
                ":z": zinc_id,
                ":zsk": zinc_sk,
                ":c": cause,
                ":corr": sorted(members - {member["incidentId"]}),
            },
        )
    return {"created": created_now, "zoneIncidentId": zinc_id, "machines": sorted(machines), "hypothesis": cause}


# ----------------------------------------------------------------------------------------------- parts
def reserve_parts(table: Any, payload: dict[str, Any], fleet: FleetConfig, now: str) -> dict[str, Any]:
    """One part for the diagnosed component (fleet-config parts[].component), from the warehouse that serves the
    zone first (fleet-config warehouses), then any other (P3).

    The guide's condition `qty - reserved >= :n` is not valid DynamoDB syntax (no arithmetic in conditions),
    so stock keeps `available` (= qty - reserved). `reservations.{incidentId}` makes retries idempotent.
    """
    inc = payload["incident"]
    component = payload["created"]["diagnosis"]["diagnosis"]["component"]
    wanted = [{"partId": p.part_id, "qty": 1} for p in fleet.parts_for_component(component)][:1]
    settings = inc.get("settings") or {}
    own = (settings.get("zoneWarehouses") or {}).get(inc["zoneId"])
    if own is not None:  # per-operation override (isolated runs)
        warehouses = list(own)
        if settings.get("partsFromOtherWarehouses", True):
            warehouses += [w for w in fleet.warehouses_for_zone(inc["zoneId"]) if w not in warehouses]
    else:
        warehouses = fleet.warehouses_for_zone(inc["zoneId"])
        if not settings.get("partsFromOtherWarehouses", True):
            warehouses = [w for w, wh in fleet.warehouses.items() if inc["zoneId"] in wh.zones]
    reserved, missing = [], []
    for part in wanted:
        done = None
        for wh in warehouses:
            stock_key = {"PK": f"PART#{part['partId']}", "SK": f"STOCK#{wh}"}
            ok = _ignore_conditional(
                table.update_item,
                Key=stock_key,
                UpdateExpression="SET available = available - :n, reserved = reserved + :n, "
                "reservations.#inc = :n, updatedAt = :now",
                ConditionExpression="available >= :n AND attribute_not_exists(reservations.#inc)",
                ExpressionAttributeNames={"#inc": inc["incidentId"]},
                ExpressionAttributeValues={":n": part["qty"], ":now": now},
            )
            row = {} if ok else (table.get_item(Key=stock_key).get("Item") or {})
            if ok or inc["incidentId"] in (row.get("reservations") or {}):  # retry of a successful reservation
                done = {"partId": part["partId"], "warehouse": wh, "qty": part["qty"]}
                break
        if done:
            reserved.append(done)
        else:
            missing.append(part)
    events = _event("PARTS_RESERVED", now, parts=len(reserved)) if reserved else []
    if missing:
        events += _event("PARTS_MISSING", now, parts=[m["partId"] for m in missing])
    table.update_item(
        Key=_incident_key(inc["machineId"], inc["incidentSK"]),
        UpdateExpression="SET partsReserved = :r, partsMissing = :m, activity = list_append(activity, :ev)",
        ExpressionAttributeValues={":r": reserved, ":m": missing, ":ev": events},
    )
    return {"reserved": reserved, "missing": missing, "hasMissing": bool(missing), "component": component}


def plan_usage(reserved: list[dict[str, Any]], used: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How much of each reservation is consumed: what the technician reported, capped by what was reserved."""
    used_qty: dict[str, int] = {}
    for u in used:
        used_qty[u["partId"]] = used_qty.get(u["partId"], 0) + int(u["qty"])
    plan = []
    for r in reserved:
        take = min(used_qty.get(r["partId"], 0), int(r["qty"]))
        used_qty[r["partId"]] = used_qty.get(r["partId"], 0) - take
        plan.append({"partId": r["partId"], "warehouse": r["warehouse"], "reserved": int(r["qty"]), "take": take})
    return plan


def _settle_parts(table: Any, incident_id: str, plan: list[dict[str, Any]], now: str) -> None:
    """Consume what was used and return the rest to `available`, exactly once per reservation: the update is
    conditional on `reservations.{incidentId}` and removes it, so a retried close changes nothing."""
    for r in plan:
        _ignore_conditional(
            table.update_item,
            Key={"PK": f"PART#{r['partId']}", "SK": f"STOCK#{r['warehouse']}"},
            UpdateExpression="SET qty = qty - :take, reserved = reserved - :res, available = available + :back, "
            "updatedAt = :now REMOVE reservations.#inc",
            ConditionExpression="attribute_exists(reservations.#inc)",
            ExpressionAttributeNames={"#inc": incident_id},
            ExpressionAttributeValues={
                ":take": r["take"],
                ":res": r["reserved"],
                ":back": r["reserved"] - r["take"],
                ":now": now,
            },
        )


# ----------------------------------------------------------------------------------------------- waits
def wait_generation(execution_id: str, stage: str, entered_time: str) -> str:
    """Public, opaque version of one wait. Same wait (Lambda/Step Functions retry) -> same value; a new wait,
    a re-armed one or a reassignment re-enters the state -> new EnteredTime -> new value, even for the same tech."""
    return hashlib.sha256(f"{execution_id}|{stage}|{entered_time}".encode()).hexdigest()[:16]


def store_task_token(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Called by .waitForTaskToken states; POST /ack resumes the execution with SendTaskSuccess.

    Stores the wait (token, stage, execution, assigned tech, generation). A late registration of an older wait
    (smaller EnteredTime) leaves the newer one intact.
    """
    inc = payload["incident"]
    stage = payload["stage"]
    execution, entered = payload.get("executionId"), payload.get("enteredTime")
    sets = "SET taskToken = :t, pendingStage = :s, pendingSince = :now"
    values: dict[str, Any] = {":t": payload["taskToken"], ":s": stage, ":now": now}
    cond = None
    generation = None
    if execution and entered:
        generation = wait_generation(execution, stage, entered)
        sets += ", waitGeneration = :g, waitExecution = :ex, waitEnteredAt = :en, waitTech = :tech"
        values.update({":g": generation, ":ex": execution, ":en": entered, ":tech": payload.get("techId") or "-"})
        cond = "attribute_not_exists(waitEnteredAt) OR waitEnteredAt <= :en"
    kwargs: dict[str, Any] = {"ConditionExpression": cond} if cond else {}
    if not _ignore_conditional(
        table.update_item,
        Key=_incident_key(inc["machineId"], inc["incidentSK"]),
        UpdateExpression=sets,
        ExpressionAttributeValues=values,
        **kwargs,
    ):
        return {"stored": None, "stale": True}
    return {"stored": stage, "generation": generation}


def certify_ack(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Workflow side of /ack: the only writer of ARRIVED/RESOLVED in the timeline.

    Records which ack the execution accepted (ackId, generation, execution, actor) as durable evidence that the
    API uses when SendTaskSuccess answers with an error or the answer was lost. Idempotent per stage.
    """
    inc = payload["incident"]
    stage = payload["stage"]
    ack = payload.get("ack") or {}
    action = "ARRIVED" if stage == "ARRIVAL" else "RESOLVED"
    key = _incident_key(inc["machineId"], inc["incidentSK"])
    evidence = _clean(
        {
            "ackId": ack.get("ackId"),
            "generation": ack.get("generation"),
            "executionId": payload.get("executionId") or ack.get("executionId"),
            "actor": ack.get("actor"),
            "by": ack.get("by"),
            "at": ack.get("at") or now,
            "certifiedAt": now,
        }
    )
    table.update_item(
        Key=key,
        UpdateExpression="SET certified = if_not_exists(certified, :empty)",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={":empty": {}},
    )
    row = from_ddb(table.get_item(Key=key, ConsistentRead=True).get("Item") or {})
    event = {"at": evidence["at"], "event": action}
    if row.get("assignedTech"):
        event["tech"] = row["assignedTech"]
    sets = "certified.#stage = :c, timeline = list_append(timeline, :ev)"
    values: dict[str, Any] = {":c": evidence, ":ev": [event]}
    if stage == "ARRIVAL":
        sets += ", #st = :state"
        values[":state"] = "IN_PROGRESS"
    first = _ignore_conditional(
        table.update_item,
        Key=key,
        UpdateExpression="SET " + sets,
        ConditionExpression="attribute_not_exists(certified.#stage)",
        ExpressionAttributeNames={"#stage": stage, **({"#st": "state"} if stage == "ARRIVAL" else {})},
        ExpressionAttributeValues=to_ddb(values),
    )
    if ack.get("ackId") and ack.get("generation"):
        _ignore_conditional(  # receipt of the API: accepted by this execution
            table.update_item,
            Key={"PK": key["PK"], "SK": f"ACK#{inc['incidentId']}#{stage}#{ack['generation']}"},
            UpdateExpression="SET #status = :acc, acceptedAt = :now",
            ConditionExpression="attribute_exists(PK) AND ackId = :id",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":acc": "ACCEPTED", ":now": now, ":id": ack["ackId"]},
        )
    return {"certified": stage, "first": first}


# ----------------------------------------------------------------------------------------------- close
def _recurrence(table: Any, inc: dict[str, Any], component: str | None, days: int) -> str | None:
    if not component or component == "UNKNOWN":
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
            and row.get("GSI1PK", "").endswith("#CLOSED")
            and not row.get("flowFailed")
            and (row.get("aiDiagnosis") or {}).get("component") == component
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
    # A retry after an interrupted close (even the next day) must finish what is missing, so there is no early
    # return here: every effect below is idempotent and the claim is only released if it still belongs to us.
    already_closed = row.get("GSI1PK", "").endswith("#CLOSED") and not row.get("flowFailed")
    if payload.get("resolution") is not None:
        certify_ack(
            table,
            {
                "incident": inc,
                "stage": "RESOLUTION",
                "ack": payload["resolution"],
                "executionId": payload.get("executionId"),
            },
            now,
        )
        row = from_ddb(table.get_item(Key=key, ConsistentRead=True).get("Item") or row)

    # Freeze what this close does on the first attempt, so a retry (Lambda or Step Functions) repeats exactly
    # the same work: same date for the MAINT# record, same parts. Then every write below is idempotent.
    resolution = payload.get("resolution") or {}
    reserved = row.get("partsReserved", [])
    used_input = resolution.get("partsUsed")
    plan = plan_usage(reserved, reserved if used_input is None else used_input)
    closing = from_ddb(
        table.update_item(
            Key=key,
            UpdateExpression="SET closing = if_not_exists(closing, :c)",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues=to_ddb({":c": {"resolvedAt": resolution.get("at") or now, "plan": plan}}),
            ReturnValues="ALL_NEW",
        )["Attributes"]["closing"]
    )
    resolved_at, plan = closing["resolvedAt"], closing["plan"]
    downtime = max(0, round((parse_iso(resolved_at) - parse_iso(inc["detectedAt"])).total_seconds() / 60))
    _settle_parts(table, inc["incidentId"], plan, now)
    used = [{"partId": r["partId"], "warehouse": r["warehouse"], "qty": r["take"]} for r in plan if r["take"]]
    component = (row.get("aiDiagnosis") or {}).get("component")
    recurrence = _recurrence(table, inc, component, int(inc["settings"].get("recurrenceWindowDays", 30)))
    tech = row.get("assignedTech")
    if tech:
        techs.release(table, tech, inc["operationId"], inc["incidentId"])

    maint_id = f"MNT-{inc['incidentId']}"
    profile = from_ddb(table.get_item(Key=keys.machine_profile(inc["machineId"])).get("Item") or {})
    diag = row.get("aiDiagnosis") or {}
    _ignore_conditional(
        table.put_item,
        Item=to_ddb(
            _clean(
                {
                    "PK": keys.machine_pk(inc["machineId"]),
                    "SK": keys.maintenance_sk(resolved_at[:10], maint_id),
                    "maintenanceId": maint_id,
                    "type": "CORRECTIVE",
                    "date": resolved_at,
                    "incidentId": inc["incidentId"],
                    "machineId": inc["machineId"],
                    "engineHours": profile.get("engineHours"),
                    "techId": tech,
                    "partsReplaced": [p["partId"] for p in used for _ in range(int(p["qty"]))],
                    "notes": resolution.get("notes")
                    or f"{diag.get('component', '')}: {diag.get('recommendedAction', '')}",
                    "downtimeMinutes": downtime,
                }
            )
        ),
        ConditionExpression="attribute_not_exists(PK)",
    )

    sets = [
        "#st = :resolved",
        "GSI1PK = :gsi1pk",
        "closedAt = :now",
        "downtimeMinutes = :dt",
        "partsUsed = :used",
        "activity = list_append(activity, :ev)",
        "maintenanceId = :mnt",
    ]
    values: dict[str, Any] = {
        ":resolved": "RESOLVED",
        ":gsi1pk": f"OP#{inc['operationId']}#INCIDENT#CLOSED",
        ":now": now,
        ":dt": downtime,
        ":used": used,
        ":mnt": maint_id,
        ":ev": _event("CLOSED", now, downtimeMinutes=downtime),
    }
    if recurrence:
        sets.append("recurrenceOf = :rec")
        values[":rec"] = recurrence
    _ignore_conditional(  # a concurrent retry may have closed it already: keep the first close
        table.update_item,
        Key=key,
        UpdateExpression="SET " + ", ".join(sets) + " REMOVE taskToken, pendingStage, waitGeneration",
        ConditionExpression="GSI1PK <> :gsi1pk",
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
        "alreadyClosed": already_closed,
        "downtimeMinutes": downtime,
        "partsUsed": used,
        "recurrenceOf": recurrence,
        "maintenanceId": maint_id,
        "techId": tech,
    }


def _leave_zone_incident(table: Any, inc: dict[str, Any], zinc_sk: str, now: str) -> None:
    zkey = {"PK": f"OP#{inc['operationId']}", "SK": zinc_sk}
    row = table.update_item(
        Key=zkey,
        UpdateExpression="DELETE openMembers :inc",
        ExpressionAttributeValues={":inc": {inc["incidentId"]}},
        ReturnValues="ALL_NEW",
    )
    if row.get("Attributes", {}).get("openMembers"):
        return
    if _ignore_conditional(
        table.update_item,
        Key=zkey,
        UpdateExpression="SET #st = :closed, closedAt = :now",
        ConditionExpression="#st = :open",
        ExpressionAttributeNames={"#st": "state"},
        ExpressionAttributeValues={":closed": "RESOLVED", ":open": "OPEN", ":now": now},
    ):
        _ignore_conditional(
            table.delete_item,
            Key={"PK": f"OP#{inc['operationId']}", "SK": f"ZONE_INCIDENT_LOCK#{inc['zoneId']}"},
            ConditionExpression="zoneIncidentSK = :zsk",
            ExpressionAttributeValues={":zsk": zinc_sk},
        )


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
