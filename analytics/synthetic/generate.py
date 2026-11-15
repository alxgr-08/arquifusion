"""Synthetic fleet history for BigQuery (P3, P6, P7, P8, P12).

Writes NDJSON files that load directly into the af_lake tables:
  telemetry.ndjson.gz       -> af_lake.telemetry        (contracts/telemetry.schema.json rows)
  incidents.ndjson          -> af_lake.incidents        (failures with component, downtime, parts)
  parts_movements.ndjson    -> af_lake.parts_movements  (daily part consumption per warehouse, 1 year)

Planted patterns the SQL must find: precursor drift 24-72 h before each failure, a contaminated fuel batch hitting
one zone, progressive fuel wear on EXC-0051, a fuel anomaly on LDR-0011, idle and overloaded machines.

Usage: python analytics/synthetic/generate.py --days 60 --out analytics/synthetic/out
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

FLEET_CONFIG = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "fleet-config.json"
STEP = timedelta(minutes=15)

# component -> (metric deltas at full precursor strength, part replaced, repair hours range)
FAILURE_MODES: dict[str, tuple[dict[str, float], str, tuple[float, float]]] = {
    "BEARING": ({"temperatureC": 14.0, "vibrationMmS": 4.0}, "BRG-SPH-22", (2.0, 6.0)),
    "HYDRAULIC_SYSTEM": ({"pressureBar": -50.0, "temperatureC": 3.0}, "FLT-HID-01", (1.5, 4.0)),
    "COOLING_SYSTEM": ({"temperatureC": 20.0}, "CLT-RAD-05", (1.0, 3.0)),
    "ENGINE": ({"fuelRateLph": 0.25, "temperatureC": 6.0, "rpm": 0.08}, "INJ-DSL-07", (2.0, 5.0)),
}
RELATIVE_METRICS = {"fuelRateLph", "rpm"}  # deltas expressed as a fraction of the normal value
NOISE = {"temperatureC": 0.025, "vibrationMmS": 0.08, "pressureBar": 0.025, "fuelRateLph": 0.05, "rpm": 0.03}


@dataclass(frozen=True)
class Failure:
    machine_id: str
    component: str
    failed_at: datetime
    precursor_hours: float
    repair_hours: float
    zone_incident: str | None = None

    @property
    def repaired_at(self) -> datetime:
        return self.failed_at + timedelta(hours=self.repair_hours)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def plan_failures(cfg: dict[str, Any], start: datetime, days: int, rng: random.Random) -> list[Failure]:
    failures: list[Failure] = []
    components = list(FAILURE_MODES)
    for machine in cfg["machines"]:
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            component = rng.choice(components)
            day = rng.uniform(5, days - 2)
            failures.append(
                Failure(
                    machine_id=machine["machineId"],
                    component=component,
                    failed_at=(start + timedelta(days=day)).replace(minute=0, second=0, microsecond=0),
                    precursor_hours=rng.uniform(24, 72),
                    repair_hours=rng.uniform(*FAILURE_MODES[component][2]),
                )
            )
    return sorted(failures, key=lambda f: f.failed_at)


def zone_fuel_event(cfg: dict[str, Any], start: datetime, days: int) -> tuple[str, datetime, datetime, str]:
    """Contaminated fuel batch in Z3, 6 hours, ~3/4 of the way through the period."""
    begin = (start + timedelta(days=int(days * 0.75))).replace(hour=9, minute=0, second=0, microsecond=0)
    return "Z3", begin, begin + timedelta(hours=6), "FB-1120-B"


def precursor_strength(ts: datetime, machine_failures: list[Failure]) -> tuple[Failure | None, float]:
    """0..1 ramp during the precursor window (quadratic, so it starts subtle)."""
    for f in machine_failures:
        window_start = f.failed_at - timedelta(hours=f.precursor_hours)
        if window_start <= ts < f.failed_at:
            progress = (ts - window_start) / (f.failed_at - window_start)
            return f, progress**2
    return None, 0.0


def in_repair(ts: datetime, machine_failures: list[Failure]) -> bool:
    return any(f.failed_at <= ts < f.repaired_at for f in machine_failures)


def telemetry_rows(
    cfg: dict[str, Any], start: datetime, days: int, failures: list[Failure], rng: random.Random
) -> Iterator[dict[str, Any]]:
    zone, fuel_begin, fuel_end, bad_batch = zone_fuel_event(cfg, start, days)
    end = start + timedelta(days=days)
    by_machine: dict[str, list[Failure]] = {}
    for f in failures:
        by_machine.setdefault(f.machine_id, []).append(f)

    for machine in cfg["machines"]:
        mid, model = machine["machineId"], cfg["models"][machine["model"]]
        normal, zone_cfg = model["normal"], cfg["zones"][machine["zoneId"]]
        own_failures = by_machine.get(mid, [])
        hours = machine["engineHours"] - days * 24 * machine["utilization"]
        lat = zone_cfg["lat"] + rng.uniform(-0.002, 0.002)
        lon = zone_cfg["lon"] + rng.uniform(-0.002, 0.002)
        operating = True
        ts = start
        while ts < end:
            day_index = (ts - start).days
            if ts.minute == 0 and ts.hour % 4 == 0:  # status decided in 4-hour blocks
                operating = rng.random() < machine["utilization"]

            failure, strength = precursor_strength(ts, own_failures)
            if in_repair(ts, own_failures):
                status = "STOPPED"
            elif not operating:
                status = "IDLE"
            else:
                status = "OPERATING"

            active = status == "OPERATING"
            metrics: dict[str, float | None] = {}
            for metric, base in normal.items():
                if metric == "speedKmh":
                    continue
                value = (
                    base
                    if active
                    else {
                        "temperatureC": base - 25,
                        "vibrationMmS": 0.8,
                        "pressureBar": base,
                        "fuelRateLph": base * 0.15,
                        "rpm": 750,
                    }[metric]
                )
                metrics[metric] = value * (1 + rng.gauss(0, NOISE[metric]))

            if active:
                load_factor = 1.15 if machine.get("overload") and rng.random() < 0.4 else rng.uniform(0.85, 1.0)
                metrics["loadTons"] = model["nominalLoadTons"] * load_factor
                metrics["speedKmh"] = normal["speedKmh"] * rng.uniform(0.7, 1.1)
                if wear := machine.get("fuelWearPctPerDay"):
                    metrics["fuelRateLph"] *= 1 + wear / 100 * day_index
                if (anomaly_days := machine.get("fuelAnomalyLastDays")) and day_index >= days - anomaly_days:
                    metrics["fuelRateLph"] *= 1.28
                if machine["zoneId"] == zone and fuel_begin <= ts < fuel_end:
                    metrics["fuelRateLph"] *= 1.32
                    metrics["temperatureC"] += 5
                if failure:
                    for metric, delta in FAILURE_MODES[failure.component][0].items():
                        if metric in RELATIVE_METRICS:
                            metrics[metric] *= 1 + delta * strength
                        else:
                            metrics[metric] += delta * strength
                hours += STEP.total_seconds() / 3600
            else:
                metrics["loadTons"] = 0.0
                metrics["speedKmh"] = 0.0

            batch = (
                bad_batch if (machine["zoneId"] == zone and fuel_begin <= ts < fuel_end) else zone_cfg["fuelBatchId"]
            )
            yield {
                "schemaVersion": "1.0",
                "messageId": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "machineId": mid,
                "operationId": cfg["operationId"],
                "model": machine["model"],
                "machineType": model["machineType"],
                "timestamp": iso(ts),
                "location": {"lat": round(lat, 5), "lon": round(lon, 5), "zoneId": machine["zoneId"]},
                "status": status,
                "metrics": {
                    "engineHours": round(hours, 2),
                    "temperatureC": round(metrics["temperatureC"], 2),
                    "vibrationMmS": round(max(metrics["vibrationMmS"], 0), 2),
                    "pressureBar": round(max(metrics["pressureBar"], 0), 1),
                    "fuelRateLph": round(max(metrics["fuelRateLph"], 0), 2),
                    "fuelLevelPct": rng.randint(20, 95),
                    "loadTons": round(metrics["loadTons"], 1),
                    "speedKmh": round(metrics["speedKmh"], 1),
                    "rpm": round(metrics["rpm"]),
                },
                "context": {"fuelBatchId": batch, "ambientTempC": round(24 + 9 * math.sin(math.pi * ts.hour / 24), 1)},
            }
            ts += STEP


def incident_rows(cfg: dict[str, Any], start: datetime, days: int, failures: list[Failure]) -> list[dict[str, Any]]:
    warehouses = {z: wh for wh, w in cfg["warehouses"].items() for z in w["zones"]}
    zone_of = {m["machineId"]: m["zoneId"] for m in cfg["machines"]}
    model_of = {m["machineId"]: m["model"] for m in cfg["machines"]}
    rows = [
        {
            "incidentId": f"INC-H{i:04d}",
            "machineId": f.machine_id,
            "model": model_of[f.machine_id],
            "operationId": cfg["operationId"],
            "zoneId": zone_of[f.machine_id],
            "component": f.component,
            "severity": "CRITICAL",
            "createdAt": iso(f.failed_at),
            "resolvedAt": iso(f.repaired_at),
            "downtimeMinutes": round(f.repair_hours * 60),
            "partsReplaced": [
                {"partId": FAILURE_MODES[f.component][1], "warehouse": warehouses[zone_of[f.machine_id]], "qty": 1}
            ],
            "zoneIncidentId": None,
            "fuelBatchId": None,
        }
        for i, f in enumerate(failures)
    ]
    zone, begin, _, batch = zone_fuel_event(cfg, start, days)
    affected = [m for m in cfg["machines"] if m["zoneId"] == zone]
    for j, m in enumerate(affected):
        created = begin + timedelta(minutes=40 + 15 * j)
        rows.append(
            {
                "incidentId": f"INC-Z{j:04d}",
                "machineId": m["machineId"],
                "model": m["model"],
                "operationId": cfg["operationId"],
                "zoneId": zone,
                "component": "FUEL_SYSTEM",
                "severity": "WARNING",
                "createdAt": iso(created),
                "resolvedAt": iso(created + timedelta(hours=1)),
                "downtimeMinutes": 60,
                "partsReplaced": [{"partId": "FLT-FUE-02", "warehouse": warehouses[zone], "qty": 1}],
                "zoneIncidentId": f"ZINC-{zone}-{begin:%Y%m%d}",
                "fuelBatchId": batch,
            }
        )
    return sorted(rows, key=lambda r: r["createdAt"])


def parts_movement_rows(cfg: dict[str, Any], end_day: date, incidents: list[dict[str, Any]], rng: random.Random):
    """365 days of daily consumption: preventive demand (weekday-heavy) + parts used by incidents."""
    rows: list[dict[str, Any]] = []
    for offset in range(365, 0, -1):
        day = end_day - timedelta(days=offset)
        weekday_factor = 1.25 if day.weekday() < 5 else 0.4
        for part in cfg["parts"]:
            for wh in part["stock"]:
                lam = part["dailyDemand"] * weekday_factor / len(part["stock"])
                qty = poisson(rng, lam)
                if part.get("irregular") and rng.random() < 0.02:
                    qty += rng.randint(4, 8)  # bulk engine overhauls: irregular consumption
                if qty:
                    rows.append(
                        {
                            "date": day.isoformat(),
                            "partId": part["partId"],
                            "warehouseId": wh,
                            "qty": qty,
                            "reason": "PREVENTIVE",
                        }
                    )
    for inc in incidents:
        for p in inc["partsReplaced"]:
            rows.append(
                {
                    "date": inc["createdAt"][:10],
                    "partId": p["partId"],
                    "warehouseId": p["warehouse"],
                    "qty": p["qty"],
                    "reason": "CORRECTIVE",
                }
            )
    return sorted(rows, key=lambda r: (r["date"], r["partId"], r["warehouseId"]))


def generate(days: int, out: Path, seed: int = 7, end: datetime | None = None) -> dict[str, int]:
    cfg = json.loads(FLEET_CONFIG.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    end = (end or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    failures = plan_failures(cfg, start, days, rng)
    out.mkdir(parents=True, exist_ok=True)

    count = 0
    with gzip.open(out / "telemetry.ndjson.gz", "wt", encoding="utf-8") as fh:
        for row in telemetry_rows(cfg, start, days, failures, rng):
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            count += 1

    incidents = incident_rows(cfg, start, days, failures)
    (out / "incidents.ndjson").write_text("\n".join(json.dumps(r) for r in incidents) + "\n", encoding="utf-8")
    movements = parts_movement_rows(cfg, end.date(), incidents, rng)
    (out / "parts_movements.ndjson").write_text("\n".join(json.dumps(r) for r in movements) + "\n", encoding="utf-8")
    return {"telemetry": count, "incidents": len(incidents), "parts_movements": len(movements)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    args = parser.parse_args()
    print(json.dumps(generate(args.days, args.out, args.seed)))


if __name__ == "__main__":
    main()
