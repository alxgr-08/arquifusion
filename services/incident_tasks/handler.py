"""af-dev-incident-tasks: task Lambda for the incident and preventive Step Functions. Owner: Rol 2.

Trigger: Step Functions (lambda:invoke). The state machine sends {"action": "<name>", ...}.
Actions are listed in ACTIONS; see statemachines/incident.asl.json and preventive.asl.json.
"""

from __future__ import annotations

from typing import Any

import incidents
import preventive
from af_shared import config
from af_shared.ddb import get_table, now_iso
from af_shared.log import get_logger

logger = get_logger("incident-tasks")
_store = config.ParamStore()
_table: Any = None


def _catalog(store: config.ParamStore) -> dict[str, Any]:
    return store.get_json(config.INCIDENT_CATALOG, default={"failureModes": [], "warehouses": []})


def dispatch(action: str, event: dict[str, Any], table: Any, store: config.ParamStore, now: str) -> dict[str, Any]:
    match action:
        case "claim":
            return incidents.claim(table, event, now, config.incident_settings(store))
        case "release_claim":
            return incidents.release_claim(table, event, now)
        case "get_profile":
            return incidents.get_profile(table, event, _catalog(store))
        case "get_history":
            return incidents.get_history(table, event)
        case "find_zone_correlation":
            return incidents.find_zone_correlation(table, event)
        case "rule_diagnosis":
            return incidents.rule_diagnosis(event)
        case "create_incident":
            return incidents.create_incident(table, event, now)
        case "create_zone_incident":
            return incidents.create_zone_incident(table, event, now)
        case "reserve_parts":
            return incidents.reserve_parts(table, event, _catalog(store), now)
        case "store_task_token":
            return incidents.store_task_token(table, event, now)
        case "close_incident":
            return incidents.close_incident(table, event, now, store)
        case "update_machine_state":
            return incidents.update_machine_state(table, event, now)
        case "load_machines":
            return preventive.load_machines(table, event)
        case "compute_due":
            return preventive.compute_due(table, event, now)
        case "create_maintenance_order":
            return preventive.create_maintenance_order(table, event, now)
        case "plan_restock":
            return preventive.plan_restock(table, event, now)
        case _:
            raise incidents.TaskError(f"Unknown action {action!r}")


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    global _table
    if _table is None:
        _table = get_table()
    action = event.get("action", "")
    inc = event.get("incident") or {}
    logger.append_keys(action=action, incidentId=inc.get("incidentId"), machineId=inc.get("machineId"))
    result = dispatch(action, event, _table, _store, now_iso())
    logger.info("task done")
    return result
