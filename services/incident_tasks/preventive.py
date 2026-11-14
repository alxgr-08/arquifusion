"""Usage-based preventive maintenance for the af-dev-preventive Step Function (P2, P3). Owner: Rol 2.

effective_interval = base_interval / usage_factor, usage_factor in [1.0, 1.5] from the last 7 daily KPIs
(written by bigquery-sync-back, Rol 3). Without KPIs yet, only the machine's current severity is used.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from af_shared import keys
from af_shared.ddb import from_ddb, is_conditional_failure, iso, parse_iso, to_ddb
from boto3.dynamodb.conditions import Key

DUE_WITHIN_HOURS = 24
# Standard preventive kit (filters). Edit here or move to SSM if the team wants it configurable.
PREVENTIVE_KIT = [{"partId": "FLT-HID-01", "qty": 1}, {"partId": "FLT-DSL-05", "qty": 1}]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def usage_factor(kpis: list[dict[str, Any]], severity: str | None = None) -> tuple[float, dict[str, float]]:
    """KPI fields expected from af_out.daily_kpis: operatingHours, overuseHours, watchHours, loadRatio."""
    operating = sum(float(k.get("operatingHours", 0) or 0) for k in kpis)
    terms = {"load": 0.0, "overuse": 0.0, "watch": 0.0}
    if kpis and operating > 0:
        load_ratio = (
            sum(float(k.get("loadRatio", 1) or 1) * float(k.get("operatingHours", 0) or 0) for k in kpis) / operating
        )
        terms["load"] = 0.2 * _clamp((load_ratio - 1.0) / 0.25, 0, 1)
        terms["overuse"] = 0.15 * _clamp(
            sum(float(k.get("overuseHours", 0) or 0) for k in kpis) / operating / 0.2, 0, 1
        )
        terms["watch"] = 0.15 * _clamp(sum(float(k.get("watchHours", 0) or 0) for k in kpis) / operating / 0.1, 0, 1)
    elif severity in ("WATCH", "WARNING", "CRITICAL"):
        terms["watch"] = 0.1
    return round(_clamp(1.0 + sum(terms.values()), 1.0, 1.5), 3), {k: round(v, 3) for k, v in terms.items()}


def load_machines(table: Any, payload: dict[str, Any]) -> dict[str, Any]:
    op = payload["operationId"]
    rows, start = [], None
    while True:
        kwargs: dict[str, Any] = {"IndexName": "GSI1", "KeyConditionExpression": Key("GSI1PK").eq(f"OP#{op}#MACHINE")}
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table.query(**kwargs)
        rows += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            break
    fields = (
        "machineId",
        "model",
        "zoneId",
        "engineHours",
        "lastMaintenanceHours",
        "maintenanceIntervalHours",
        "status",
        "severity",
    )
    machines = []
    for row in map(from_ddb, rows):
        m = {f: row.get(f) for f in fields}
        m["machineId"] = m["machineId"] or row["PK"].removeprefix("MACHINE#")
        machines.append(m)
    return {"operationId": op, "machines": machines}


def compute_due(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    m = payload["machine"]
    since = iso(parse_iso(now) - timedelta(days=7))[:10]
    res = table.query(
        KeyConditionExpression=Key("PK").eq(keys.machine_pk(m["machineId"]))
        & Key("SK").between(f"KPI#{since}", f"KPI#{now[:10]}")
    )
    kpis = [from_ddb(i) for i in res["Items"]]
    factor, terms = usage_factor(kpis, m.get("severity"))
    base = float(m.get("maintenanceIntervalHours") or 500)
    effective = base / factor
    engine = float(m.get("engineHours") or 0)
    last = float(m.get("lastMaintenanceHours") or 0)
    remaining = last + effective - engine
    return {
        **m,
        "operationId": payload["operationId"],
        "usageFactor": factor,
        "factorTerms": terms,
        "baseIntervalHours": base,
        "effectiveIntervalHours": round(effective, 1),
        "remainingHours": round(remaining, 1),
        "isDue": remaining < DUE_WITHIN_HOURS,
        "kpiDays": len(kpis),
    }


def _available(table: Any, part_id: str) -> int:
    res = table.query(KeyConditionExpression=Key("PK").eq(f"PART#{part_id}") & Key("SK").begins_with("STOCK#"))
    return sum(int(i.get("available", 0)) for i in res["Items"])


def create_maintenance_order(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """One preventive order per machine per day (idempotent), with parts availability check."""
    m = payload["due"]
    date = now[:10]
    order_id = f"PM-{m['machineId']}-{date.replace('-', '')}"
    parts = [{**p, "available": _available(table, p["partId"])} for p in PREVENTIVE_KIT]
    parts_ok = all(p["available"] >= p["qty"] for p in parts)
    try:
        table.put_item(
            Item=to_ddb(
                {
                    "PK": keys.machine_pk(m["machineId"]),
                    "SK": keys.maintenance_sk(date, order_id),
                    "maintenanceId": order_id,
                    "type": "PREVENTIVE",
                    "state": "SCHEDULED",
                    "machineId": m["machineId"],
                    "zoneId": m.get("zoneId"),
                    "date": date,
                    "createdAt": now,
                    "usageFactor": m["usageFactor"],
                    "effectiveIntervalHours": m["effectiveIntervalHours"],
                    "remainingHours": m["remainingHours"],
                    "partsPlanned": [{"partId": p["partId"], "qty": p["qty"]} for p in parts],
                    "partsOk": parts_ok,
                }
            ),
            ConditionExpression="attribute_not_exists(PK)",
        )
        created = True
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise
        created = False
    return {
        "orderId": order_id,
        "machineId": m["machineId"],
        "zoneId": m.get("zoneId"),
        "created": created,
        "remainingHours": m["remainingHours"],
        "usageFactor": m["usageFactor"],
        "parts": parts,
        "partsOk": parts_ok,
    }


def group_orders(payload: dict[str, Any]) -> dict[str, Any]:
    by_zone: dict[str, list[dict[str, Any]]] = {}
    for result in payload.get("results", []):
        order = result.get("order") if isinstance(result, dict) else None
        if order:
            by_zone.setdefault(order.get("zoneId") or "?", []).append(order)
    return {"byZone": by_zone, "orders": sum(len(v) for v in by_zone.values())}


def plan_restock(table: Any, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Compare stock with the BigQuery forecast for next week plus today's preventive needs (P3)."""
    grouped = group_orders(payload)
    need: dict[str, int] = {}
    for orders in grouped["byZone"].values():
        for o in orders:
            for p in o["parts"]:
                need[p["partId"]] = need.get(p["partId"], 0) + int(p["qty"])
    next_week = parse_iso(now) + timedelta(days=7)
    iso_week = f"{next_week.isocalendar().year}-W{next_week.isocalendar().week:02d}"
    reorders = []
    part_ids = sorted({*need, *(p["partId"] for p in PREVENTIVE_KIT)} | set(payload.get("watchParts", [])))
    for part_id in part_ids:
        rows = [from_ddb(i) for i in table.query(KeyConditionExpression=Key("PK").eq(f"PART#{part_id}"))["Items"]]
        stock = {r["warehouseId"]: r for r in rows if r["SK"].startswith("STOCK#")}
        forecast = {
            r["SK"].split("#")[1]: float(r.get("demand", r.get("forecast", 0)) or 0)
            for r in rows
            if r["SK"].startswith("FORECAST#") and r["SK"].endswith(iso_week)
        }
        for wh, row in stock.items():
            demand = forecast.get(wh, 0.0) + (need.get(part_id, 0) / max(len(stock), 1))
            available = int(row.get("available", 0))
            if available < demand:
                reorders.append(
                    {
                        "partId": part_id,
                        "warehouseId": wh,
                        "available": available,
                        "forecastDemand": round(demand, 1),
                        "suggestedQty": int(demand - available + 0.999),
                    }
                )
    lines = [f"Ordenes preventivas hoy: {grouped['orders']}"]
    lines += [
        f"- {zone}: {', '.join(o['machineId'] for o in orders)}" for zone, orders in sorted(grouped["byZone"].items())
    ]
    if reorders:
        lines.append(
            "Reposicion sugerida: "
            + "; ".join(f"{r['partId']} x{r['suggestedQty']} en {r['warehouseId']}" for r in reorders)
        )
    return {**grouped, "forecastWeek": iso_week, "reorders": reorders, "summary": "\n".join(lines)}
