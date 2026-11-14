"""Technician assignment (P5). Owner: Rol 2.

cost = ETA_min + 60 * (lacks required specialty) + 15 * (incidents already in the technician's queue)
ETA from Amazon Location CalculateRouteMatrix when AF_ROUTE_CALCULATOR is set; otherwise straight-line distance
x 1.3 (mine roads) at technicianSpeedKmh. The cheapest technician is claimed with a conditional write.
"""

from __future__ import annotations

from typing import Any

from af_shared import keys, techs
from af_shared.ddb import from_ddb, is_conditional_failure, to_ddb
from af_shared.fleet import haversine_km
from boto3.dynamodb.conditions import Key

ROAD_FACTOR = 1.3
NO_SPECIALTY_PENALTY = 60
QUEUE_PENALTY = 15


def candidates(table: Any, operation_id: str) -> list[dict[str, Any]]:
    res = table.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"OP#{operation_id}#TECH"))
    return [from_ddb(i) for i in res["Items"] if i.get("availability") in ("AVAILABLE", "BUSY")]


def eta_minutes(
    techs_: list[dict[str, Any]],
    dest: tuple[float, float],
    speed_kmh: float,
    location: Any = None,
    calculator: str | None = None,
) -> tuple[list[float], str]:
    """Returns (eta per technician, method)."""
    if location is not None and calculator and techs_:
        try:
            res = location.calculate_route_matrix(
                CalculatorName=calculator,
                DeparturePositions=[[t["lon"], t["lat"]] for t in techs_],
                DestinationPositions=[[dest[1], dest[0]]],
                TravelMode="Car",
            )
            etas = [row[0].get("DurationSeconds") for row in res["RouteMatrix"]]
            if all(e is not None for e in etas):
                return [e / 60 for e in etas], "AMAZON_LOCATION"
        except Exception:  # route not found off-road, service blocked in the lab, etc.
            pass
    return [
        haversine_km(t["lat"], t["lon"], dest[0], dest[1]) * ROAD_FACTOR / speed_kmh * 60 for t in techs_
    ], "HAVERSINE"


def rank(techs_: list[dict[str, Any]], etas: list[float], specialty: str | None) -> list[dict[str, Any]]:
    ranked = []
    for t, eta in zip(techs_, etas, strict=True):
        lacks = bool(specialty) and specialty not in t.get("specialties", [])
        queue = int(t.get("queue", 0))
        cost = eta + NO_SPECIALTY_PENALTY * lacks + QUEUE_PENALTY * queue
        ranked.append(
            {
                "techId": t["techId"],
                "name": t.get("name"),
                "etaMinutes": max(1, round(eta)),
                "hasSpecialty": not lacks,
                "queue": queue,
                "cost": round(cost, 1),
            }
        )
    return sorted(ranked, key=lambda r: (r["cost"], r["techId"]))


def assign(
    table: Any, payload: dict[str, Any], now: str, location: Any = None, calculator: str | None = None
) -> dict[str, Any]:
    inc = payload["incident"]
    previous = payload.get("assignment") or {}
    excluded = set(previous.get("excluded", []))
    attempt = int(previous.get("attempt", 0)) + 1
    settings = inc.get("settings", {})
    profile = payload.get("profile") or {}
    specialty = ((payload.get("diagnosis") or {}).get("diagnosis") or {}).get("component")

    if profile.get("lat") is None:
        row = from_ddb(table.get_item(Key=keys.machine_profile(inc["machineId"])).get("Item") or {})
        profile = {"lat": row.get("lat"), "lon": row.get("lon")}
    everyone = candidates(table, inc["operationId"])
    base = {"assigned": False, "attempt": attempt, "excluded": sorted(excluded), "candidates": []}
    holder = next((t for t in everyone if inc["incidentId"] in (t.get("incidentQueue") or [])), None)
    if holder:  # Step Functions retried after a successful claim: keep that technician
        return {
            **base,
            "assigned": True,
            "techId": holder["techId"],
            "techName": holder.get("name"),
            "etaMinutes": None,
            "retried": True,
        }
    pool = [t for t in everyone if t["techId"] not in excluded]
    if not pool or profile.get("lat") is None:
        return base

    etas, method = eta_minutes(
        pool, (profile["lat"], profile["lon"]), float(settings.get("technicianSpeedKmh", 25)), location, calculator
    )
    ranked = rank(pool, etas, specialty)
    for choice in ranked:
        tech_id = choice["techId"]
        try:
            table.update_item(
                Key={"PK": f"TECH#{tech_id}", "SK": "PROFILE"},
                UpdateExpression="SET availability = :a, GSI1SK = :sk, currentIncidentId = :inc "
                "ADD #q :one, incidentQueue :incset",
                ConditionExpression="availability IN (:avail, :a) AND NOT contains(incidentQueue, :inc)",
                ExpressionAttributeNames={"#q": "queue"},
                ExpressionAttributeValues={
                    ":a": "BUSY",
                    ":avail": "AVAILABLE",
                    ":inc": inc["incidentId"],
                    ":one": 1,
                    ":incset": {inc["incidentId"]},
                    ":sk": keys.technician_keys(tech_id, inc["operationId"], "BUSY")["GSI1SK"],
                },
            )
        except Exception as exc:
            if is_conditional_failure(exc):
                continue  # went off shift, or a retry of this same assignment: try the next candidate
            raise
        table.put_item(
            Item=to_ddb(
                {
                    **keys.assignment_keys(tech_id, now, inc["incidentId"]),
                    "techId": tech_id,
                    "incidentId": inc["incidentId"],
                    "machineId": inc["machineId"],
                    "assignedAt": now,
                    "etaMinutes": choice["etaMinutes"],
                    "cost": choice["cost"],
                    "etaMethod": method,
                }
            )
        )
        table.update_item(
            Key={"PK": keys.machine_pk(inc["machineId"]), "SK": inc["incidentSK"]},
            UpdateExpression="SET assignedTech = :t, #st = :s, etaMinutes = :eta, assignmentCandidates = :c, "
            "timeline = list_append(timeline, :ev)",
            ExpressionAttributeNames={"#st": "state"},
            ExpressionAttributeValues=to_ddb(
                {
                    ":t": tech_id,
                    ":s": "ASSIGNED",
                    ":eta": choice["etaMinutes"],
                    ":c": ranked[:3],
                    ":ev": [{"at": now, "event": "ASSIGNED", "tech": tech_id}],
                }
            ),
        )
        return {
            **base,
            "assigned": True,
            "techId": tech_id,
            "techName": choice.get("name"),
            "etaMinutes": choice["etaMinutes"],
            "cost": choice["cost"],
            "hasSpecialty": choice["hasSpecialty"],
            "etaMethod": method,
            "candidates": ranked[:3],
        }
    return {**base, "candidates": ranked[:3]}


def release(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Technician did not confirm in time: free them, exclude them, record it, and let the flow reassign."""
    inc = payload["incident"]
    assignment = payload.get("assignment") or {}
    tech_id = assignment.get("techId")
    if tech_id:
        techs.release(table, tech_id, inc["operationId"], inc["incidentId"])
    table.update_item(
        Key={"PK": keys.machine_pk(inc["machineId"]), "SK": inc["incidentSK"]},
        UpdateExpression="SET #st = :s, activity = list_append(activity, :ev) "
        "REMOVE assignedTech, etaMinutes, taskToken",
        ExpressionAttributeNames={"#st": "state"},
        ExpressionAttributeValues={
            ":s": "OPEN",
            ":ev": [{"at": now, "event": "ACK_TIMEOUT", "tech": tech_id or "-"}],
        },
    )
    excluded = sorted({*assignment.get("excluded", []), *([tech_id] if tech_id else [])})
    return {"assigned": False, "attempt": 0, "excluded": excluded, "timedOutTech": tech_id, "candidates": []}
