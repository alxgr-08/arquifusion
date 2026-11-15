"""af-dev-fault-injector: POST /simulator/faults — inject or clear demo faults. Owner: Rol 2.

Body (what the frontend sends):
  {"machineId": "EXC-0042", "faultType": "BEARING_WEAR"}       # BEARING_WEAR | OVERHEATING | HYDRAULIC_LEAK
  {"machineId": "EXC-0042", "faultType": "FUEL_BATCH_ZONE"}    # P11: every machine in EXC-0042's zone
  {"action": "CLEAR", "machineId": "EXC-0042"}                 # or no machineId = clear all
Optional: "durationMinutes" (default 60), "intensity" (multiplier, default 1.0), "zoneId" instead of machineId.
Extra demo types: SENSOR_FAILURE (temperature null), STOPPED.
Active faults live in SSM /af/sim/faults; the simulator re-reads them every few seconds.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from af_shared import config
from af_shared.ddb import iso, parse_iso
from af_shared.fleet import FleetConfig
from af_shared.http import ApiError, error, json_body, require_group, respond
from af_shared.log import get_logger
from fleet_faults import FAULT_TYPES, ZONE_FAULTS

logger = get_logger("fault-injector")
_store = config.ParamStore()
_lambda: Any = None
MAX_ACTIVE = 25


def allowed_groups() -> set[str]:
    return set(os.environ.get("AF_FAULT_GROUPS", "MAINT_LEAD,SUPERVISOR,MANAGER").split(","))


def prune(faults: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return [f for f in faults if not f.get("expiresAt") or parse_iso(f["expiresAt"]) > now]


def apply(body: dict[str, Any], cfg: FleetConfig, faults: list[dict[str, Any]], now: datetime) -> tuple[list, dict]:
    """Pure core: returns (new fault list, response body)."""
    faults = prune(faults, now)
    action = str(body.get("action", "INJECT")).upper()

    if action == "CLEAR":
        machine_id = body.get("machineId")
        kept = [f for f in faults if machine_id and f["machineId"] != machine_id]
        return kept, {"cleared": len(faults) - len(kept), "activeFaults": kept}
    if action != "INJECT":
        raise ApiError(400, "INVALID_ACTION", "action must be INJECT or CLEAR")

    fault_type = body.get("faultType")
    if fault_type not in FAULT_TYPES:
        raise ApiError(400, "UNKNOWN_FAULT", f"faultType must be one of {sorted(FAULT_TYPES)}")
    try:
        intensity = float(body.get("intensity", 1.0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "INVALID_INTENSITY", "intensity must be a number (1.0 = normal)") from exc
    if not 0.1 <= intensity <= 3:
        raise ApiError(400, "INVALID_INTENSITY", "intensity must be between 0.1 and 3")
    duration = float(body.get("durationMinutes", 60))
    if not 1 <= duration <= 24 * 60:
        raise ApiError(400, "INVALID_DURATION", "durationMinutes must be between 1 and 1440")

    zone_id = body.get("zoneId")
    if body.get("machineId"):
        machine = cfg.machine(body["machineId"])
        if machine is None:
            raise ApiError(404, "MACHINE_NOT_FOUND", f"Unknown machine {body['machineId']}")
        targets = [machine.machine_id]
        if fault_type in ZONE_FAULTS:
            zone_id = machine.zone_id
    elif not zone_id:
        raise ApiError(400, "MISSING_TARGET", "Send machineId or zoneId")
    if zone_id and (fault_type in ZONE_FAULTS or not body.get("machineId")):
        targets = [m.machine_id for m in cfg.machines if m.zone_id == zone_id]
        if not targets:
            raise ApiError(404, "ZONE_NOT_FOUND", f"No machines in zone {zone_id}")

    fault_id = f"F-{secrets.token_hex(2)}"
    new = []
    for machine_id in targets:
        entry = {
            "faultId": fault_id,
            "machineId": machine_id,
            "faultType": fault_type,
            "intensity": intensity,
            "startedAt": iso(now),
            "expiresAt": iso(now + timedelta(minutes=duration)),
        }
        if zone_id:
            entry["zoneId"] = zone_id
        new.append(entry)

    # one active fault per machine and type: re-injecting replaces it
    replaced = {(f["machineId"], f["faultType"]) for f in new}
    faults = [f for f in faults if (f["machineId"], f["faultType"]) not in replaced] + new
    if len(faults) > MAX_ACTIVE:
        raise ApiError(409, "TOO_MANY_FAULTS", f"At most {MAX_ACTIVE} active faults; send action CLEAR first")
    return faults, {
        "faultId": fault_id,
        "faultType": fault_type,
        "intensity": intensity,
        "affectedMachines": targets,
        "expiresAt": new[0]["expiresAt"],
    }


def handle(event: dict[str, Any], store: config.ParamStore, kick: Any = None) -> dict[str, Any]:
    try:
        require_group(event, allowed_groups())
        body = json_body(event)
        cfg = FleetConfig.model_validate(store.get_json(config.FLEET_CONFIG))
        current = (store.get_json(config.SIM_FAULTS, default={"faults": []}, fresh=True) or {}).get("faults", [])
        faults, result = apply(body, cfg, current, datetime.now(UTC))
        store.put_json(config.SIM_FAULTS, {"faults": faults})
        logger.info(
            "faults updated",
            action=body.get("action", "INJECT"),
            active=len(faults),
            machineId=body.get("machineId"),
            zoneId=body.get("zoneId"),
        )
        if kick and "faultId" in result:
            kick()
        return respond(201 if "faultId" in result else 200, result)
    except ApiError as exc:
        return error(exc)


def _kick_simulator() -> None:
    """Start the fast loop now instead of waiting for the next minute tick."""
    global _lambda
    name = os.environ.get("AF_SIMULATOR_FUNCTION")
    if not name:
        return
    if _lambda is None:
        import boto3

        _lambda = boto3.client("lambda")
    _lambda.invoke(FunctionName=name, InvocationType="Event", Payload=json.dumps({"fastOnly": True}).encode())


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    return handle(event, _store, kick=_kick_simulator)
