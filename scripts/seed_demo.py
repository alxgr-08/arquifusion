"""Seed the demo fleet into DynamoDB and SSM from contracts/examples/fleet-config.json. Owner: Rol 2.

Usage (lab credentials in ~/.aws/credentials):
    python scripts/seed_demo.py --demo            # items + SSM, short timeouts for the video
    python scripts/seed_demo.py --only-ssm        # refresh parameters without touching table state
    python scripts/seed_demo.py --dry-run         # print what would be written

Writes:
  DynamoDB  OP#/META, OP#/ZONE#, OP#/WH#, MACHINE#/PROFILE, MODEL#/BASELINE (until bigquery-sync-back runs),
            TECH#/PROFILE, PART#/META, PART#/STOCK#
  SSM       /af/sim/fleet-config (the whole fleet-config.json), /af/sim/faults, /af/incident/settings
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from af_shared import config, keys  # noqa: E402
from af_shared.ddb import now_iso, to_ddb  # noqa: E402
from af_shared.fleet import METRIC_NAMES, FleetConfig  # noqa: E402

DEMO_SETTINGS = {
    "techAckTimeoutSeconds": 90,
    "resolutionTimeoutSeconds": 600,
    "noTechnicianRetrySeconds": 30,
}


def build_items(raw: dict[str, Any], now: str | None = None) -> list[dict[str, Any]]:
    cfg = FleetConfig.model_validate(raw)
    now = now or now_iso()
    op = cfg.operation_id
    items: list[dict[str, Any]] = [
        {
            "PK": f"OP#{op}",
            "SK": "META",
            "operationId": op,
            "zones": sorted(cfg.zones),
            "warehouses": sorted(cfg.warehouses),
        },
    ]
    for zone_id, z in cfg.zones.items():
        items.append(
            {
                "PK": f"OP#{op}",
                "SK": f"ZONE#{zone_id}",
                "zoneId": zone_id,
                "name": z.name,
                "lat": z.lat,
                "lon": z.lon,
                "fuelBatchId": z.fuel_batch_id,
            }
        )
    for wh_id, wh in cfg.warehouses.items():
        items.append({"PK": f"OP#{op}", "SK": f"WH#{wh_id}", "warehouseId": wh_id, "zones": wh.zones})
    for name, model in cfg.models.items():
        metrics = {m: {"mean": model.normal_value(m), "std": round(model.std(m), 4), "n": 0} for m in METRIC_NAMES}
        items.append(
            {
                **keys.model_baseline_keys(name),
                "model": name,
                "machineType": model.machine_type,
                "metrics": metrics,
                "source": "SEED",
            }
        )
    for m in cfg.machines:
        model = cfg.model_of(m.model)
        zone = cfg.zone(m.zone_id)
        items.append(
            {
                **keys.machine_profile(m.machine_id),
                **keys.machine_gsi1(op, "OPERATING", m.machine_id),
                "machineId": m.machine_id,
                "operationId": op,
                "model": m.model,
                "machineType": model.machine_type,
                "zoneId": m.zone_id,
                "lat": zone.lat,
                "lon": zone.lon,
                "status": "OPERATING",
                "healthScore": 100,
                "severity": "OK",
                "anomalyStreak": 0,
                "engineHours": m.engine_hours,
                "lastMaintenanceHours": m.last_maintenance_hours,
                "maintenanceIntervalHours": model.interval_hours,
                "nominalLoadTons": model.nominal_load_tons,
                "fuelBatchId": zone.fuel_batch_id,
                "lastSeen": now,
            }
        )
    for t in cfg.technicians:
        items.append(
            {
                **keys.technician_keys(t.tech_id, op, "AVAILABLE"),
                "techId": t.tech_id,
                "operationId": op,
                "name": t.name,
                "specialties": t.specialties,
                "lat": t.lat,
                "lon": t.lon,
                "availability": "AVAILABLE",
                "queue": 0,
            }
        )
    for p in cfg.parts:
        items.append(
            {
                "PK": f"PART#{p.part_id}",
                "SK": "META",
                "partId": p.part_id,
                "name": p.name,
                "component": p.component,
                "unitCost": p.unit_cost,
                "dailyDemand": p.daily_demand,
            }
        )
        total = sum(p.stock.values()) or 1
        for wh, qty in p.stock.items():
            weekly = p.daily_demand * 7 * qty / total
            items.append(
                {
                    **keys.stock_keys(p.part_id, wh),
                    "partId": p.part_id,
                    "warehouseId": wh,
                    "name": p.name,
                    "component": p.component,
                    "qty": qty,
                    "available": qty,
                    "reserved": 0,
                    "reservations": {},
                    "reorderPoint": max(1, math.ceil(weekly)),
                }
            )
    return [to_ddb({k: v for k, v in i.items() if v is not None}) for i in items]


def build_parameters(raw: dict[str, Any], demo: bool) -> dict[str, Any]:
    return {
        config.FLEET_CONFIG: raw,
        config.SIM_FAULTS: {"faults": []},
        config.INCIDENT_SETTINGS: {**config.DEFAULT_INCIDENT_SETTINGS, **(DEMO_SETTINGS if demo else {})},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "contracts/examples/fleet-config.json"))
    parser.add_argument("--demo", action="store_true", help="short Step Function timeouts for the video")
    parser.add_argument("--only-ssm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    items = [] if args.only_ssm else build_items(raw)
    params = build_parameters(raw, args.demo)
    if args.dry_run:
        print(f"{len(items)} items, parameters: {', '.join(params)}")
        return 0

    from af_shared.ddb import get_table

    with get_table().batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    store = config.ParamStore()
    for name, value in params.items():
        store.put_json(name, value)
    print(f"Seeded {len(items)} items and {len(params)} SSM parameters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
