"""Synthetic Lambda delivery records shaped like the items currently written by Rol 2 and Rol 3."""

import json
from pathlib import Path

from af_shared.ddb import to_ddb
from boto3.dynamodb.types import TypeSerializer

ARN = "arn:aws:dynamodb:us-east-1:123456789012:table/af-dev-arquifusion/stream/2026-11-14T00:00:00.000"
BUCKET = "af-dev-arquifusion-lake-test"
OWNER = "123456789012"
EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "examples"
TS = "2026-11-13T13:05:00Z"


def sample(file="telemetry.ok.json"):
    return json.loads((EXAMPLES / file).read_text(encoding="utf-8"))


def record(seq, pk, sk, new, old=None):
    keys = {"PK": pk, "SK": sk}
    marshal = TypeSerializer().serialize
    change = {"SequenceNumber": str(seq), "Keys": marshal(keys)["M"], "StreamViewType": "NEW_AND_OLD_IMAGES"}
    if old is not None:
        change["OldImage"] = marshal(to_ddb({**old, **keys}))["M"]
    if new is not None:
        change["NewImage"] = marshal(to_ddb({**new, **keys}))["M"]
    return {
        "eventSource": "aws:dynamodb",
        "eventSourceARN": ARN,
        "eventID": f"local-{seq}",
        "eventName": "REMOVE" if new is None else ("INSERT" if old is None else "MODIFY"),
        "dynamodb": change,
    }


def reading(seq, raw):
    return record(
        seq,
        "MACHINE#" + raw["machineId"],
        "READING#" + raw["timestamp"],
        {"rawTelemetry": raw, "messageId": raw["messageId"]},
    )


def incidents():
    opened = {
        "incidentId": "INC-local",
        "machineId": "SYN-0001",
        "operationId": "OP-TEST",
        "model": "SYN",
        "zoneId": "Z-1",
        "severity": "WARNING",
        "createdAt": TS,
        "state": "OPEN",
        "aiDiagnosis": {"component": "BEARING", "privateDetail": "PRIVATE-FIXTURE"},
        "GSI1PK": "OP#OP-TEST#INCIDENT#OPEN",
        "activity": [],
        "taskToken": "PRIVATE-FIXTURE",
    }
    assigned = {**opened, "state": "ASSIGNED", "assignedTech": "PRIVATE-FIXTURE"}
    closed = {
        **assigned,
        "state": "RESOLVED",
        "closedAt": "2026-11-13T14:05:00Z",
        "downtimeMinutes": 60,
        "GSI1PK": "OP#OP-TEST#INCIDENT#CLOSED",
        "partsUsed": [{"partId": "P-1", "warehouse": "W-1", "qty": 1}],
    }
    keys = ("MACHINE#SYN-0001", f"INCIDENT#{TS}#INC-local")
    return [record(21, *keys, opened), record(22, *keys, assigned, opened), record(23, *keys, closed, assigned)]


def stock_events():
    initial = {"qty": 5, "reserved": 0, "reservations": {}, "available": 5, "updatedAt": TS}
    reserved = {**initial, "reserved": 2, "available": 3, "reservations": {"INC-local": 2}}
    consumed = {**initial, "qty": 4, "available": 4}
    keys = ("PART#P-1", "STOCK#W-1")
    return [
        record(31, *keys, initial),
        record(32, *keys, reserved, initial),
        record(33, *keys, consumed, reserved),
        record(34, *keys, initial, reserved),
        record(35, *keys, {**initial, "qty": 9, "available": 9}, initial),
        record(36, *keys, consumed, initial),
    ]
