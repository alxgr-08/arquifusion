"""Files for one isolated rehearsal run of the incident flow in AWS Academy (docs/ensayo-lab/README.md). Owner: Rol 2.

    python scripts/ensayo_kit.py --run 7 --out ensayo-0007

Everything belongs to the run: operation ENSAYO-0007, zone ZE, machine ENS-0007, technician T-E0007,
warehouse WH-E0007, operator grant ensayo-operador-0007. Settings and the zone -> warehouse mapping live in the
run's own DynamoDB row (OP#ENSAYO-0007 / INCIDENT_SETTINGS), so no shared SSM parameter is touched.
`time`/`detectedAt` are generated now (UTC) and stay the same if the same event file is sent again.
Writes nothing to AWS: it only creates files (items in DynamoDB JSON for `put-item`).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

from af_shared import grants  # noqa: E402

GOOGLE_ISS = "https://accounts.google.com"
LAB_SETTINGS = {
    "techAckTimeoutSeconds": 600,
    "resolutionTimeoutSeconds": 1800,
    "noTechnicianRetrySeconds": 60,
    "maxAssignAttempts": 2,
    "partsFromOtherWarehouses": False,
}


def ids(run: int) -> dict[str, str]:
    n = f"{run:04d}"
    return {
        "run": n,
        "operation": f"ENSAYO-{n}",
        "zone": "ZE",
        "machine": f"ENS-{n}",
        "tech": f"T-E{n}",
        "warehouse": f"WH-E{n}",
        "sub": f"ensayo-operador-{n}",
    }


def _ddb(item: dict[str, Any]) -> dict[str, Any]:
    from boto3.dynamodb.types import TypeSerializer

    ser = TypeSerializer()

    def conv(v: Any) -> Any:
        if isinstance(v, float):
            return Decimal(str(v))
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return {k: ser.serialize(conv(v)) for k, v in item.items()}


def items(i: dict[str, str], now: str) -> dict[str, dict[str, Any]]:
    op, wh = i["operation"], i["warehouse"]
    if i["run"] == "0001":
        # The public rehearsal uses synthetic positions in Cerro de Pasco, Peru.
        zone_point = (-10.6830, -76.2530)
        machine_point = (-10.6829, -76.2528)
        technician_point = (-10.6851, -76.2540)
    else:
        zone_point = (-23.601, -69.978)
        machine_point = (-23.6009, -69.9775)
        technician_point = (-23.603, -69.979)
    return {
        # Operation metadata the web needs (GET /parts uses META.warehouses; zones for the map and filters)
        "item-op-meta.json": {
            "PK": f"OP#{op}",
            "SK": "META",
            "operationId": op,
            "run": i["run"],
            "zones": [i["zone"]],
            "warehouses": [wh],
        },
        "item-op-zone.json": {
            "PK": f"OP#{op}",
            "SK": f"ZONE#{i['zone']}",
            "zoneId": i["zone"],
            "run": i["run"],
            "name": f"Zona ensayo {i['run']}",
            "lat": zone_point[0],
            "lon": zone_point[1],
            "fuelBatchId": f"FB-E{i['run']}",
        },
        "item-op-warehouse.json": {
            "PK": f"OP#{op}",
            "SK": f"WH#{wh}",
            "warehouseId": wh,
            "run": i["run"],
            "zones": [i["zone"]],
        },
        "item-op-settings.json": {
            "PK": f"OP#{op}",
            "SK": "INCIDENT_SETTINGS",
            "run": i["run"],
            "createdAt": now,
            "settings": LAB_SETTINGS,
            "zoneWarehouses": {i["zone"]: [wh]},
        },
        "item-machine.json": {
            "PK": f"MACHINE#{i['machine']}",
            "SK": "PROFILE",
            "GSI1PK": f"OP#{op}#MACHINE",
            "GSI1SK": f"OPERATING#{i['machine']}",
            "machineId": i["machine"],
            "operationId": op,
            "run": i["run"],
            "model": "EXC-320",
            "machineType": "EXCAVATOR",
            "zoneId": i["zone"],
            "lat": machine_point[0],
            "lon": machine_point[1],
            "status": "OPERATING",
            "healthScore": 100,
            "severity": "OK",
            "anomalyStreak": 0,
            "engineHours": 12450.0,
            "lastMaintenanceHours": 12000.0,
            "maintenanceIntervalHours": 500.0,
            "nominalLoadTons": 18.0,
        },
        "item-technician.json": {
            "PK": f"TECH#{i['tech']}",
            "SK": "PROFILE",
            "GSI1PK": f"OP#{op}#TECH",
            "GSI1SK": f"AVAILABLE#{i['tech']}",
            "techId": i["tech"],
            "operationId": op,
            "run": i["run"],
            "name": f"Técnico ensayo {i['run']}",
            "specialties": ["DRIVETRAIN", "BEARING"],
            "lat": technician_point[0],
            "lon": technician_point[1],
            "availability": "AVAILABLE",
            "queue": 0,
        },
        "item-grant-operador.json": grants.item(
            GOOGLE_ISS, i["sub"], op, "MAINT_LEAD", label=f"Operador del ensayo {i['run']} (Rol 1)"
        ),
        "item-stock-caso2.json": {
            "PK": "PART#BRG-SPH-22",
            "SK": f"STOCK#{wh}",
            "GSI1PK": f"WH#{wh}",
            "GSI1SK": "PART#BRG-SPH-22",
            "partId": "BRG-SPH-22",
            "warehouseId": wh,
            "run": i["run"],
            "name": "Rodamiento esférico",
            "component": "BEARING",
            "qty": 1,
            "available": 1,
            "reserved": 0,
            "reservations": {},
            "reorderPoint": 0,
        },
    }


def anomaly(i: dict[str, str], at: str, signals: list[dict[str, Any]], case: int) -> dict[str, Any]:
    return {
        "version": "0",
        "id": f"ensayo-{i['run']}-caso{case}",
        "source": "arquifusion.health",
        "detail-type": "AnomalyDetected",
        "account": "000000000000",
        "region": "us-east-1",
        "time": at,
        "resources": [],
        "detail": {
            "schemaVersion": "1.0",
            "machineId": i["machine"],
            "operationId": i["operation"],
            "zoneId": i["zone"],
            "model": "EXC-320",
            "detectedAt": at,
            "healthScore": 55,
            "severity": "WARNING",
            "signals": signals,
            "comparedAgainst": ["OWN_HISTORY", "MODEL_FLEET"],
        },
    }


def http_event(i: dict[str, str], method: str, suffix: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """HTTP API v2 event for `aws lambda invoke af-dev-api-ack` (no API Gateway yet). Replace INC-XXXX and
    ETAG-XXXX. In AWS only API Gateway fills `authorizer.jwt.claims`; a direct invoke is an IAM-only test."""
    path = f"/machines/{i['machine']}/incidents/INC-XXXX{suffix}"
    event: dict[str, Any] = {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "headers": {},
        "requestContext": {
            "http": {"method": method},
            "stage": "$default",
            "authorizer": {"jwt": {"claims": {"iss": GOOGLE_ISS, "sub": i["sub"]}}},
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        event["headers"] = {"if-match": '"ETAG-XXXX"'}
        event["body"] = json.dumps(body, ensure_ascii=False)
    return event


def build(run: int, now: datetime | None = None) -> dict[str, Any]:
    i = ids(run)
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    at1 = now.isoformat().replace("+00:00", "Z")
    at2 = (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    rows = items(i, at1)
    files: dict[str, Any] = {name: _ddb(item) for name, item in rows.items()}
    files["anomaly-caso1-vibracion.json"] = anomaly(
        i,
        at1,
        [
            {"metric": "vibrationMmS", "value": 7.8, "baseline": 4.2, "zScore": 3.1, "trend": "RISING"},
            {"metric": "temperatureC", "value": 80.1, "baseline": 78.1, "zScore": 0.6, "trend": "STABLE"},
        ],
        1,
    )
    files["anomaly-caso2-rodamiento.json"] = anomaly(
        i,
        at2,
        [
            {"metric": "vibrationMmS", "value": 7.8, "baseline": 4.2, "zScore": 3.1, "trend": "RISING"},
            {"metric": "temperatureC", "value": 92.4, "baseline": 78.1, "zScore": 3.0, "trend": "RISING"},
        ],
        2,
    )
    files["api-get-incident.json"] = http_event(i, "GET", "")
    files["api-ack-arrived.json"] = http_event(i, "POST", "/ack", {"action": "ARRIVED"})
    files["api-ack-resolved.json"] = http_event(
        i, "POST", "/ack", {"action": "RESOLVED", "notes": f"Ensayo {i['run']}"}
    )
    files["grants-participantes.json"] = [  # fill in each person's Google `sub`; keep this file out of git
        {
            "sub": "<sub de Google>",
            "label": "<nombre> (jefe de mantenimiento)",
            "operationId": i["operation"],
            "role": "MAINT_LEAD",
        },
        {
            "sub": "<sub de Google>",
            "label": "<nombre> (técnico asignado)",
            "operationId": i["operation"],
            "role": "TECHNICIAN",
            "techId": i["tech"],
        },
        {
            "sub": "<sub de Google>",
            "label": "<nombre> (supervisor)",
            "operationId": i["operation"],
            "role": "SUPERVISOR",
        },
    ]
    files["run.json"] = {
        **i,
        "generatedAt": at1,
        "rows": [{"PK": r["PK"], "SK": r["SK"], "file": name} for name, r in rows.items()],
        "rowsCreatedByTheFlow": [
            f"MACHINE#{i['machine']} / INCIDENT#…, MAINT#…, ACK#… (incident, maintenance, ack receipts)",
            f"TECH#{i['tech']} / ASSIGN#… (assignment history)",
            f"OP#{i['operation']} / ZINC#… only if a zone incident is opened (not expected)",
        ],
    }
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=int, required=True, help="run number (unique per rehearsal)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in build(args.run).items():
        (out / name).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Run {args.run:04d} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
