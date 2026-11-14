"""Seed the demo fleet into DynamoDB and SSM from contracts/examples/fleet-config.json. Owner: Rol 2.

Usage (lab credentials in ~/.aws/credentials):
    python scripts/seed_demo.py --demo            # items + SSM, short timeouts for the video
    python scripts/seed_demo.py --only-ssm        # refresh parameters without touching table state
    python scripts/seed_demo.py --dry-run         # print what would be written

Writes:
  DynamoDB  OP#/META, OP#/ZONE#, OP#/WH#, MACHINE#/PROFILE, MODEL#/BASELINE, TECH#/PROFILE, PART#/META, PART#/STOCK#
  SSM       /af/sim/fleet-config, /af/sim/faults, /af/incident/catalog, /af/incident/settings
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from af_shared import config, keys  # noqa: E402
from af_shared.ddb import to_ddb  # noqa: E402
from af_shared.fleet import INCIDENT_KEYS, SIMULATOR_KEYS, FleetConfig, subset  # noqa: E402

DEMO_SETTINGS = {
    "techAckTimeoutSeconds": 90,
    "resolutionTimeoutSeconds": 600,
    "noTechnicianRetrySeconds": 30,
}


def build_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = FleetConfig.model_validate(raw)
    op = cfg.operation_id
    items: list[dict[str, Any]] = [
        {
            "PK": f"OP#{op}",
            "SK": "META",
            "operationId": op,
            "referenceTime": cfg.reference_time,
            "zones": [z.zone_id for z in cfg.zones],
            "warehouses": [w.warehouse_id for w in cfg.warehouses],
        },
    ]
    items += [{"PK": f"OP#{op}", "SK": f"ZONE#{z.zone_id}", **z.model_dump(by_alias=True)} for z in cfg.zones]
    items += [{"PK": f"OP#{op}", "SK": f"WH#{w.warehouse_id}", **w.model_dump(by_alias=True)} for w in cfg.warehouses]
    for model in cfg.models:
        items.append(
            {
                **keys.model_baseline_keys(model.model),
                "model": model.model,
                "machineType": model.machine_type,
                "source": "SEED",
                "metrics": {k: v.model_dump() for k, v in model.metrics.items()},
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
                "maintenanceIntervalHours": model.maintenance_interval_hours,
                "nominalLoadTons": model.nominal_load_tons,
                "costPerHourUsd": model.cost_per_hour_usd,
                "fuelBatchId": m.fuel_batch_id,
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
                "queueLength": 0,
            }
        )
    for p in cfg.parts:
        items.append(
            {
                "PK": f"PART#{p.part_id}",
                "SK": "META",
                "partId": p.part_id,
                "name": p.name,
                "unitCostUsd": p.unit_cost_usd,
                "leadTimeDays": p.lead_time_days,
            }
        )
        for wh, qty in p.stock.items():
            items.append(
                {
                    **keys.stock_keys(p.part_id, wh),
                    "partId": p.part_id,
                    "warehouseId": wh,
                    "name": p.name,
                    "qty": qty,
                    "available": qty,
                    "reserved": 0,
                    "reservations": {},
                }
            )
    return [to_ddb(i) for i in items]


def build_parameters(raw: dict[str, Any], demo: bool) -> dict[str, Any]:
    return {
        config.FLEET_CONFIG: subset(raw, SIMULATOR_KEYS),
        config.SIM_FAULTS: {"faults": []},
        config.INCIDENT_CATALOG: subset(raw, INCIDENT_KEYS),
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
