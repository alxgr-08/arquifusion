"""Deterministic, stateless fleet simulator (P4 demo, feeds P1/P6/P7/P8). Owner: Rol 2.

Every value is a pure function of (machine, timestamp, active faults): no state is kept between invocations,
retries produce the same messageId (so the health-evaluator deduplicates them) and tests are reproducible.
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from datetime import datetime
from typing import Any

from af_shared.ddb import iso, parse_iso
from af_shared.fleet import FleetConfig, Machine

IDLE_FACTORS = {"loadTons": 0.0, "speedKmh": 0.0, "fuelRateLph": 0.3, "vibrationMmS": 0.4}
DECIMALS = {
    "temperatureC": 1,
    "vibrationMmS": 2,
    "pressureBar": 1,
    "fuelRateLph": 1,
    "loadTons": 1,
    "speedKmh": 1,
    "rpm": 0,
}


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


def simulate(cfg: FleetConfig, machine: Machine, now: datetime, faults: list[dict[str, Any]]) -> dict[str, Any]:
    model = cfg.model_of(machine.model)
    zone = cfg.zone(machine.zone_id)
    ts = iso(now)
    hours = (now - parse_iso(cfg.reference_time)).total_seconds() / 3600
    static = _rng(machine.machine_id, "static")  # the machine's own personality, stable over time
    noise = _rng(machine.machine_id, ts)
    mine = active_faults(faults, machine.machine_id, now)

    # Status: duty cycle proportional to utilization, phase-shifted per machine.
    phase = static.random()
    status = "OPERATING" if ((hours + phase) % 1.0) < machine.utilization else "IDLE"
    if any(cfg.fault_catalog.get(f["faultType"]) and cfg.fault_catalog[f["faultType"]].sigma for f in mine):
        status = "OPERATING"  # a degrading machine keeps working: that is the point of predictive maintenance

    metrics: dict[str, float | None] = {}
    for name, stat in model.metrics.items():
        offset = static.gauss(0, 0.3 * stat.std)
        value = stat.mean + offset + noise.gauss(0, stat.std)
        if status == "IDLE":
            if name == "rpm":
                value = 750 + noise.gauss(0, 30)
            elif name == "temperatureC":
                value -= 8
            else:
                value *= IDLE_FACTORS.get(name, 1.0)
        metrics[name] = value

    batch = machine.fuel_batch_id
    ramp_s = cfg.demo.ramp_seconds if cfg.demo else 60
    for fault in mine:
        spec = cfg.fault_catalog.get(fault["faultType"])
        if spec is None:
            continue
        elapsed = (now - parse_iso(fault["startedAt"])).total_seconds()
        ramp = 1.0 if ramp_s <= 0 else min(max(elapsed / ramp_s, 0.0), 1.0)
        intensity = float(fault.get("sigma") or cfg.fault_intensity.get(fault.get("intensity", "WARNING"), 3.5))
        for name, direction in spec.sigma.items():
            if metrics.get(name) is not None:
                metrics[name] = metrics[name] + direction * intensity * model.metrics[name].std * ramp
        for name in spec.null_metrics:
            metrics[name] = None
        if spec.status:
            status = spec.status
        if spec.fuel_batch_id:
            batch = spec.fuel_batch_id

    if status in ("STOPPED", "MAINTENANCE"):
        for name in ("loadTons", "speedKmh", "fuelRateLph"):
            if metrics.get(name) is not None:
                metrics[name] = 0.0
        if metrics.get("rpm") is not None:
            metrics["rpm"] = 0.0

    clean: dict[str, float | None] = {}
    for name, value in metrics.items():
        clean[name] = None if value is None else round(max(value, 0.0), DECIMALS.get(name, 1))
        if name == "rpm" and value is not None:
            clean[name] = float(round(max(value, 0.0)))

    burn_per_hour = model.metrics["fuelRateLph"].mean * machine.utilization / model.fuel_tank_l * 100
    fuel_level = 100 - ((hours * burn_per_hour + static.uniform(0, 80)) % 85)

    angle = 2 * math.pi * static.random() + hours * 0.8
    radius_km = zone.radius_km * (0.3 + 0.6 * static.random())
    lat = zone.lat + (radius_km * math.sin(angle)) / 111.0
    lon = zone.lon + (radius_km * math.cos(angle)) / (111.0 * math.cos(math.radians(zone.lat)))

    return {
        "schemaVersion": "1.0",
        "messageId": str(uuid.UUID(int=noise.getrandbits(128), version=4)),
        "machineId": machine.machine_id,
        "operationId": cfg.operation_id,
        "model": machine.model,
        "machineType": model.machine_type,
        "timestamp": ts,
        "location": {"lat": round(lat, 6), "lon": round(lon, 6), "zoneId": machine.zone_id},
        "status": status,
        "metrics": {
            "engineHours": round(machine.engine_hours + max(hours, 0) * machine.utilization, 2),
            **clean,
            "fuelLevelPct": round(fuel_level, 1),
        },
        "context": {
            "fuelBatchId": batch,
            "ambientTempC": round(22 + 9 * math.sin(2 * math.pi * ((now.hour + now.minute / 60) - 9) / 24), 1),
        },
    }


def topic(message: dict[str, Any]) -> str:
    return f"arquifusion/{message['operationId']}/{message['machineId']}/telemetry"
