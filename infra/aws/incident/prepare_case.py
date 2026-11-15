"""Prepare an isolated incident case and AWS CLI input files without contacting AWS."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from af_shared import keys
from boto3.dynamodb.types import TypeSerializer
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]


def prepare_case(parent: Path) -> Path:
    run = uuid4()
    operation = f"AF-INC-TEST-{run.hex}"
    letters = "".join(chr(65 + byte % 26) for byte in run.bytes[:3])
    machine = f"IN{letters}-{run.int % 10000:04d}"
    tech = f"TEST-{run.hex}"
    zone = f"ZONE-{run.hex}"
    model = f"MODEL-{run.hex}"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    examples = ROOT / "contracts" / "examples"
    event = json.loads((examples / "anomaly-detected.json").read_text(encoding="utf-8"))
    # Both diagnosis implementations classify isolated vibration as DRIVETRAIN.
    # reserve_parts looks up global part IDs, even for a synthetic operation.
    fleet = json.loads((examples / "fleet-config.json").read_text(encoding="utf-8"))
    if any(p["component"] == "DRIVETRAIN" for p in fleet["parts"]):
        raise ValueError("The shared catalog now has drivetrain stock. Review test isolation.")
    event["detail"].update(
        operationId=operation,
        machineId=machine,
        model=model,
        zoneId=zone,
        detectedAt=now,
        signals=[s for s in event["detail"]["signals"] if s["metric"] == "vibrationMmS"],
        healthScore=52,
        severity="WARNING",
    )
    schema = json.loads((ROOT / "contracts" / "anomaly-detected.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    validator.validate(event)
    profiles = [
        {
            **keys.machine_profile(machine),
            **keys.machine_gsi1(operation, "OPERATING", machine),
            "machineId": machine,
            "operationId": operation,
            "model": model,
            "zoneId": zone,
            "status": "OPERATING",
            "lat": -12,
            "lon": -77,
            "engineHours": 100,
            "lastMaintenanceHours": 80,
            "lastSeen": now,
        },
        {
            **keys.technician_keys(tech, operation, "AVAILABLE"),
            "techId": tech,
            "operationId": operation,
            "name": f"Test {run.hex[:8]}",
            "specialties": ["DRIVETRAIN"],
            "lat": -12,
            "lon": -77,
            "availability": "AVAILABLE",
            "queue": 0,
        },
    ]
    serializer = TypeSerializer()
    seed = {
        "ClientRequestToken": run.hex,
        "TransactItems": [
            {
                "Put": {
                    "TableName": "af-dev-arquifusion",
                    "Item": serializer.serialize(row)["M"],
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
            for row in profiles
        ],
    }

    case = parent / run.hex
    case.mkdir(parents=True)

    def save(filename: str, data: object) -> None:
        (case / filename).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    save("seed.json", seed)
    save("test.tfvars.json", {"incident_test_operation_id": operation})
    # The outer EventBridge ID is assigned by the service on PutEvents.
    envelope = {
        "version": "0",
        "id": str(run),
        "account": "000000000000",
        "region": "us-east-1",
        "time": now,
        "resources": [],
        **event,
    }
    save("envelope.json", envelope)
    save(
        "put-events.json",
        [
            {
                "EventBusName": "af-dev-bus",
                "Source": event["source"],
                "DetailType": event["detail-type"],
                "Detail": json.dumps(event["detail"]),
            }
        ],
    )
    return case


if __name__ == "__main__":
    print(prepare_case(ROOT / "build" / "incident" / "cases"))
