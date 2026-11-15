"""Append confirmed, unique business facts to the existing af_lake tables using batch load jobs."""

import re
from datetime import date, datetime

from google.cloud import bigquery

from services.lake_to_bigquery.load_jobs import append_config, load_file
from services.lake_to_bigquery.telemetry import bigquery_row, telemetry_row
from services.stream_exporter.lake_io import CHUNK, PENDING, ROOT, TABLES, DataError, canonical, digest

INCIDENT_FIELDS = {
    "incidentId",
    "machineId",
    "model",
    "operationId",
    "zoneId",
    "component",
    "severity",
    "createdAt",
    "resolvedAt",
    "downtimeMinutes",
    "partsReplaced",
    "zoneIncidentId",
    "fuelBatchId",
}


def required_string(row, name):
    if not isinstance(row.get(name), str) or not row[name]:
        raise DataError(f"invalid {name}")


def validate_row(kind, row):
    if kind == "telemetry":
        return telemetry_row(row)
    if not isinstance(row, dict):
        raise DataError("invalid fact")
    if kind == "parts_movements":
        if set(row) != {"date", "partId", "warehouseId", "reason", "qty"}:
            raise DataError("invalid movement columns")
        for field in ("partId", "warehouseId", "reason"):
            required_string(row, field)
        if type(row["qty"]) is not int or row["qty"] <= 0 or date.fromisoformat(row["date"]).isoformat() != row["date"]:
            raise DataError("invalid movement")
    elif kind == "incidents":
        if row.keys() - INCIDENT_FIELDS:
            raise DataError("invalid incident columns")
        for field in ("incidentId", "machineId", "createdAt", "resolvedAt"):
            required_string(row, field)
        for field in ("createdAt", "resolvedAt"):
            if datetime.fromisoformat(row[field]).tzinfo is None:
                raise DataError("incident time missing offset")
        for field in INCIDENT_FIELDS - {"partsReplaced", "downtimeMinutes"}:
            if row.get(field) is not None and not isinstance(row[field], str):
                raise DataError("invalid incident attribute")
        duration = row.get("downtimeMinutes")
        if duration is not None and (type(duration) is not int or duration < 0):
            raise DataError("invalid downtimeMinutes")
        if not isinstance(row.get("partsReplaced"), list):
            raise DataError("missing incident parts list")
        for part in row["partsReplaced"]:
            if not isinstance(part, dict) or set(part) != {"partId", "warehouse", "qty"}:
                raise DataError("invalid incident parts")
            required_string(part, "partId")
            required_string(part, "warehouse")
            if type(part["qty"]) is not int or part["qty"] <= 0:
                raise DataError("invalid incident part quantity")
    else:
        raise DataError("unexpected destination")
    return row


def claim_rows(archive, project, kind, batch, entries):
    unique = {}
    for item in entries:
        fact_id = item["id"]
        if not re.fullmatch(r"[0-9a-f]{64}", fact_id):
            raise DataError("invalid fact ID")
        row = validate_row(kind, item["row"])
        if kind == "telemetry" and fact_id != digest(canonical(row["messageId"])):
            raise DataError("telemetry fact ID disagrees with messageId")
        if fact_id in unique and unique[fact_id] != row:
            raise DataError("conflicting revisions of the same canonical fact")
        unique[fact_id] = row

    owned = []
    for fact_id, row in sorted(unique.items()):
        path = f"{ROOT}control/{project}/facts/{kind}/{fact_id}.json"
        owner = {"batch": batch, "rowSha256": digest(canonical(row))}
        if not archive.put_new(path, canonical(owner)):
            stored = archive.json(path)
            if not stored or stored["rowSha256"] != owner["rowSha256"]:
                raise DataError("canonical fact differs from its durable owner")
            if stored["batch"] != batch:
                continue  # The original owner's pending manifest was written before it could claim this fact.
        owned.append(row)
    return owned


def append_hash(bucket, destination, batch, rows):
    # Preserve existing job IDs for manifests whose final append was already submitted.
    return digest(canonical([bucket, destination, batch, digest(canonical(rows))]))


def existing_messages(client, destination, rows, *, timeout):
    expected = {row["messageId"]: row for row in rows}
    ids = list(expected)
    present = set()
    sql = (
        "SELECT schemaVersion, messageId, machineId, operationId, model, machineType, "
        f"timestamp, location, status, metrics, context FROM `{destination}` "
        "WHERE LOWER(messageId) IN UNNEST(@ids)"
    )
    for offset in range(0, len(ids), CHUNK):
        config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids[offset : offset + CHUNK])],
        )
        for record in client.query(sql, job_config=config, location="US").result(timeout=timeout):
            row = bigquery_row(record)
            message_id = row["messageId"]
            if message_id in present or expected.get(message_id) != row:
                raise DataError("existing telemetry conflicts with this messageId")
            present.add(message_id)
    return sorted(present)


def telemetry_append_plan(archive, client, destination, batch, owned, *, timeout):
    control = f"{ROOT}control/{client.project}"
    path = f"{control}/plans/telemetry/{batch}.json"
    plan = archive.json(path)
    ownership = digest(canonical(owned))
    if plan is None:
        legacy = append_hash(archive.bucket, destination, batch, owned)
        # Older manifests did not freeze a selection. Resume their original, potentially committed job.
        present = (
            []
            if archive.json(f"{control}/jobs/{legacy}.json")
            else existing_messages(client, destination, owned, timeout=timeout)
        )
        plan = {"ownedSha256": ownership, "alreadyPresent": present}
        if not archive.put_new(path, canonical(plan)):
            plan = archive.json(path)
    if not plan or plan.get("ownedSha256") != ownership:
        raise DataError("telemetry append plan conflicts with its owners")
    present = set(plan["alreadyPresent"])
    if len(present) != len(plan["alreadyPresent"]) or not present.issubset(row["messageId"] for row in owned):
        raise DataError("invalid existing messageIds in telemetry append plan")
    return [row for row in owned if row["messageId"] not in present], len(present)


def load_manifest(archive, client, pending_key, *, timeout=10):
    match = re.fullmatch(re.escape(PENDING) + r"([a-z_]+)/([a-f0-9]{64})\.json", pending_key)
    if not match or match[1] not in TABLES:
        raise DataError("unexpected pending key")
    kind, batch = match.groups()
    control = f"{ROOT}control/{client.project}"
    done_key = f"{control}/done/{kind}/{batch}.json"
    done = archive.json(done_key)
    if done is not None:
        archive.acknowledge(pending_key)
        return done
    manifest = archive.json(pending_key)
    if manifest is None:  # a concurrent worker may just have acknowledged it
        done = archive.json(done_key)
        if done is None:
            raise DataError("missing manifest and completion receipt")
        return done
    if manifest["kind"] != kind or manifest["batch"] != batch:
        raise DataError("manifest destination mismatch")
    entries = archive.batch(kind, batch, manifest["rows"])
    rows = claim_rows(archive, client.project, kind, batch, entries)
    destination = f"{client.project}.af_lake.{kind}"
    present = 0
    if kind == "telemetry" and rows:
        rows, present = telemetry_append_plan(archive, client, destination, batch, rows, timeout=timeout)
    job_id = None
    if rows:
        payload = b"".join(canonical(row) + b"\n" for row in rows)
        job_hash = append_hash(archive.bucket, destination, batch, rows)
        job = load_file(
            archive, client, destination, payload, append_config(), job_hash, expected_rows=len(rows), timeout=timeout
        )
        job_id = job.job_id
    done = {
        "batch": batch,
        "table": kind,
        "job": job_id,
        "loadedRows": len(rows),
        "duplicateOrDelegatedRows": len(entries) - len(rows),
    }
    if present:
        done["alreadyPresentRows"] = present
    if not archive.put_new(done_key, canonical(done)) and archive.json(done_key) != done:
        raise DataError("completion receipt conflict")
    archive.acknowledge(pending_key)
    return done
