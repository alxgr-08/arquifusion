"""Deterministic, stateless fleet simulator (P4 demo, feeds P1/P6/P7/P8). Owner: Rol 2.

Every value is a pure function of (machine, timestamp, active faults): no state between invocations, retries
produce the same messageId (the health-evaluator deduplicates them) and tests are reproducible.
Fault effects match the frontend mock (frontend/src/mock/mockApi.ts) so the web and the backend tell the same story.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from af_shared.ddb import iso, parse_iso
from af_shared.fleet import SPREAD_PCT, FleetConfig, Machine

DECIMALS = {
    "temperatureC": 1,
    "vibrationMmS": 2,
    "pressureBar": 1,
    "fuelRateLph": 1,
    "loadTons": 1,
    "speedKmh": 1,
    "rpm": 0,
}
REFERENCE_TIME = "2026-11-14T00:00:00Z"
CONTAMINATED_SUFFIX = "-X"


def _bearing(m: dict[str, float | None], p: float) -> None:
    _add(m, "temperatureC", 15 * p)
    _add(m, "vibrationMmS", 4.2 * p)


def _overheating(m: dict[str, float | None], p: float) -> None:
    _add(m, "temperatureC", 22 * p)


def _hydraulic(m: dict[str, float | None], p: float) -> None:
    _add(m, "pressureBar", -55 * p)


def _fuel_batch(m: dict[str, float | None], p: float) -> None:
    if m.get("fuelRateLph") is not None:
        m["fuelRateLph"] = m["fuelRateLph"] * (1 + 0.35 * p)
    _add(m, "temperatureC", 5 * p)


def _sensor(m: dict[str, float | None], p: float) -> None:
    m["temperatureC"] = None


def _add(m: dict[str, float | None], metric: str, delta: float) -> None:
    if m.get(metric) is not None:
        m[metric] = m[metric] + delta


# Names used by the frontend (FaultType in frontend/src/api/types.ts) + two extra demo helpers.
FAULTS: dict[str, Callable[[dict[str, float | None], float], None]] = {
    "BEARING_WEAR": _bearing,
    "OVERHEATING": _overheating,
    "HYDRAULIC_LEAK": _hydraulic,
    "FUEL_BATCH_ZONE": _fuel_batch,
    "SENSOR_FAILURE": _sensor,
    "STOPPED": lambda m, p: None,
}
ZONE_FAULTS = {"FUEL_BATCH_ZONE"}


def _rng(*parts: object) -> random.Random:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def active_faults(faults: list[dict[str, Any]], machine_id: str, now: datetime) -> list[dict[str, Any]]:
    out = []
    for f in faults:
        if f.get("machineId") != machine_id or parse_iso(f["startedAt"]) > now:
            continue
        if f.get("expiresAt") and parse_iso(f["expiresAt"]) <= now:
            continue
        out.append(f)
    return out


def simulate(
    cfg: FleetConfig, machine: Machine, now: datetime, faults: list[dict[str, Any]], ramp_seconds: float = 300
) -> dict[str, Any]:
    model = cfg.model_of(machine.model)
    zone = cfg.zone(machine.zone_id)
    ts = iso(now)
    hours = (now - parse_iso(REFERENCE_TIME)).total_seconds() / 3600
    static = _rng(machine.machine_id, "static")  # the machine's own personality, stable over time
    noise = _rng(machine.machine_id, ts)
    mine = active_faults(faults, machine.machine_id, now)

    phase = static.random()
    status = "OPERATING" if ((hours + phase) % 1.0) < machine.utilization else "IDLE"
    if any(f["faultType"] != "STOPPED" for f in mine):
        status = "OPERATING"  # a degrading machine keeps working: that is the point of predictive maintenance

    metrics: dict[str, float | None] = {}
    for name in DECIMALS:
        normal = model.normal_value(name)
        offset = 1 + (static.random() - 0.5) * SPREAD_PCT[name] * 0.5  # small, stable per-machine bias
        value = normal * offset * (1 + (noise.random() - 0.5) * SPREAD_PCT[name])
        if status == "IDLE":
            value = {
                "temperatureC": normal - 20,
                "vibrationMmS": 0.8,
                "fuelRateLph": normal * 0.15,
                "loadTons": 0.0,
                "speedKmh": 0.0,
                "rpm": 750 * (1 + (noise.random() - 0.5) * 0.03),
            }.get(name, value)
        metrics[name] = value

    batch = zone.fuel_batch_id
    for fault in mine:
        effect = FAULTS.get(fault["faultType"])
        if effect is None:
            continue
        elapsed = (now - parse_iso(fault["startedAt"])).total_seconds()
        progress = 1.0 if ramp_seconds <= 0 else min(max(elapsed / ramp_seconds, 0.0), 1.0)
        effect(metrics, progress * float(fault.get("intensity", 1.0)))
        if fault["faultType"] == "STOPPED":
            status = "STOPPED"
        if fault["faultType"] in ZONE_FAULTS and batch:
            batch = batch + CONTAMINATED_SUFFIX

    if status in ("STOPPED", "MAINTENANCE"):
        for name in ("loadTons", "speedKmh", "fuelRateLph", "rpm"):
            if metrics.get(name) is not None:
                metrics[name] = 0.0

    clean: dict[str, float | None] = {}
    for name, value in metrics.items():
        clean[name] = None if value is None else round(max(value, 0.0), DECIMALS[name])
        if name == "rpm" and value is not None:
            clean[name] = float(round(max(value, 0.0)))

    fuel_level = 100 - ((hours * 6 * machine.utilization + static.uniform(0, 80)) % 85)
    angle = 2 * math.pi * static.random() + hours * 0.8
    radius = 0.0015 + 0.0015 * static.random()  # degrees, ~150-330 m around the zone centre
    context: dict[str, Any] = {
        "ambientTempC": round(22 + 9 * math.sin(2 * math.pi * ((now.hour + now.minute / 60) - 9) / 24), 1)
    }
    if batch:
        context["fuelBatchId"] = batch

    return {
        "schemaVersion": "1.0",
        "messageId": str(uuid.UUID(int=noise.getrandbits(128), version=4)),
        "machineId": machine.machine_id,
        "operationId": cfg.operation_id,
        "model": machine.model,
        "machineType": model.machine_type,
        "timestamp": ts,
        "location": {
            "lat": round(zone.lat + radius * math.sin(angle), 6),
            "lon": round(zone.lon + radius * math.cos(angle), 6),
            "zoneId": machine.zone_id,
        },
        "status": status,
        "metrics": {
            "engineHours": round(machine.engine_hours + max(hours, 0) * machine.utilization, 2),
            **clean,
            "fuelLevelPct": round(fuel_level, 1),
        },
        "context": context,
    }


def topic(message: dict[str, Any]) -> str:
    return f"arquifusion/{message['operationId']}/{message['machineId']}/telemetry"
