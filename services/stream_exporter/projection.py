"""Project only analytical fields from NEW_AND_OLD_IMAGES; the single table also contains private workflow data."""

from datetime import UTC, datetime

from af_shared.ddb import from_ddb
from af_shared.keys import incident_sk, machine_pk
from boto3.dynamodb.types import TypeDeserializer

from services.stream_exporter.lake_io import DataError, entry

DESERIALIZER = TypeDeserializer()


def field(image, name, fallback=None):
    return from_ddb(DESERIALIZER.deserialize(image[name])) if name in image else fallback


def text_field(value):
    if not isinstance(value, str) or not value:
        raise DataError("missing required string")
    return value


def utc(value):
    try:
        instant = datetime.fromisoformat(text_field(value))
        if instant.tzinfo is None:
            raise DataError("date must include UTC offset")
        return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        raise DataError("invalid timestamp") from None


def incident_image(image, pk, sk):
    inc = text_field(field(image, "incidentId"))
    machine = text_field(field(image, "machineId"))
    created = text_field(field(image, "createdAt"))
    if pk != machine_pk(machine) or sk != incident_sk(created, inc):
        raise DataError("incident keys disagree with its identity")
    row = {k: field(image, k) for k in ("model", "operationId", "zoneId", "severity", "zoneIncidentId", "fuelBatchId")}
    row.update(
        incidentId=inc,
        machineId=machine,
        createdAt=utc(created),
        component=(field(image, "aiDiagnosis") or {}).get("component"),
        partsReplaced=[],
    )
    closed_at = field(image, "closedAt")
    if closed_at:
        row["resolvedAt"] = utc(closed_at)
    if "downtimeMinutes" in image:
        row["downtimeMinutes"] = field(image, "downtimeMinutes")
    for part in field(image, "partsUsed", []):
        if type(part.get("qty")) is not int or part["qty"] <= 0:
            raise DataError("invalid parts used")
        row["partsReplaced"].append(
            {
                "partId": text_field(part.get("partId")),
                "warehouse": text_field(part.get("warehouse") or part.get("warehouseId")),
                "qty": part["qty"],
            }
        )
    terminal = (
        field(image, "state") == "RESOLVED"
        and field(image, "GSI1PK") == f"OP#{row['operationId']}#INCIDENT#CLOSED"
        and not field(image, "flowFailed", False)
        and bool(closed_at)
    )
    if row.get("downtimeMinutes") is not None and (
        type(row["downtimeMinutes"]) is not int or row["downtimeMinutes"] < 0
    ):
        raise DataError("invalid downtime")
    return row, terminal


def stock(old, new, pk, sk, source, stream):
    before, after = field(old, "qty"), field(new, "qty")
    if type(before) is not int or type(after) is not int or min(before, after) < 0:
        raise DataError("invalid stock quantity")
    if before <= after:  # includes reserve/release without consumption, stock receipt and replenishment
        return None
    used = before - after
    old_res, new_res = field(old, "reservations", {}), field(new, "reservations", {})
    if not isinstance(old_res, dict) or not isinstance(new_res, dict):
        raise DataError("invalid inventory reservation map")
    old_count, new_count = field(old, "reserved"), field(new, "reserved")
    removed = set(old_res) - set(new_res)
    if len(removed) == 1 and all(old_res.get(k) == v for k, v in new_res.items()):
        owner = next(iter(removed))
        held = old_res[owner]
        tracked = (
            type(held) is int
            and type(old_count) is int
            and type(new_count) is int
            and 0 <= new_count <= after
            and 0 <= old_count <= before
            and old_count - new_count == held
            and used <= held
            and field(old, "available") == before - old_count
            and field(new, "available") == after - new_count
            and owner.startswith("INC-")
        )
        if tracked:
            row = {
                "date": utc(field(new, "updatedAt"))[:10],
                "partId": pk.removeprefix("PART#"),
                "warehouseId": sk.removeprefix("STOCK#"),
                "qty": used,
                "reason": "CORRECTIVE",
            }
            return "parts_movements", entry([stream, source], source, row)
    return "stock_review", entry(
        [stream, source],
        source,
        {
            "partId": pk.removeprefix("PART#"),
            "warehouseId": sk.removeprefix("STOCK#"),
            "qtyBefore": before,
            "qtyAfter": after,
            "reason": "UNCLASSIFIED_DECREASE",
        },
    )


def project(record, expected_arn):
    if record.get("eventSource") != "aws:dynamodb" or record.get("eventSourceARN") != expected_arn:
        raise DataError("unexpected stream")
    op = record["eventName"]
    if op == "REMOVE":
        return []
    if op not in {"INSERT", "MODIFY"}:
        raise DataError("unsupported stream operation")
    change = record["dynamodb"]
    pk, sk = (text_field(field(change["Keys"], key)) for key in ("PK", "SK"))
    incident = pk.startswith("MACHINE#") and sk.startswith("INCIDENT#")
    stock_change = pk.startswith("PART#") and sk.startswith("STOCK#") and op == "MODIFY"
    if not (incident or stock_change):
        return []
    source = text_field(record.get("eventID"))
    new = change["NewImage"]
    old = change["OldImage"] if op == "MODIFY" else {}
    if any(field(im, "PK") != pk or field(im, "SK") != sk for im in ([new, old] if old else [new])):
        raise DataError("stream images disagree with keys")
    if stock_change:
        movement = stock(old, new, pk, sk, source, expected_arn)
        return [movement] if movement else []
    row, terminal = incident_image(new, pk, sk)
    status, failed = field(new, "state"), field(new, "flowFailed", False)
    if (
        old
        and (row, terminal) == incident_image(old, pk, sk)
        and (status, failed) == (field(old, "state"), field(old, "flowFailed", False))
    ):
        return []  # taskToken, ACK and other backend-only updates need no snapshot
    results = [
        ("incident_history", entry([expected_arn, source], source, {**row, "state": status, "flowFailed": failed}))
    ]
    if terminal:
        results.append(("incidents", entry([pk, sk], source, row)))
    return results
