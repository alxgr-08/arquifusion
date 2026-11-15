"""JSON parameters from SSM Parameter Store (SecureString, KMS), cached per Lambda container.

Global variables live in SSM, never in code (hackathon rule). Names follow /af/<area>/<name>.
"""

from __future__ import annotations

import json
import time
from typing import Any

FLEET_CONFIG = "/af/sim/fleet-config"
SIM_FAULTS = "/af/sim/faults"
INCIDENT_SETTINGS = "/af/incident/settings"
NOTIFY_TOPICS = "/af/notify/topics"

DEFAULT_INCIDENT_SETTINGS: dict[str, Any] = {
    "techAckTimeoutSeconds": 1800,  # demo: 60
    "resolutionTimeoutSeconds": 14400,  # >4 h stopped escalates to management
    "noTechnicianRetrySeconds": 120,
    "maxAssignAttempts": 5,
    "zoneWindowMinutes": 120,
    "zoneMinMachines": 3,
    "recurrenceWindowDays": 30,
    "technicianSpeedKmh": 25,
}


class ParamStore:
    def __init__(self, client: Any | None = None, ttl_seconds: float = 60.0) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("ssm")
        return self._client

    def get_json(self, name: str, default: Any = None, *, fresh: bool = False) -> Any:
        hit = self._cache.get(name)
        if hit and not fresh and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        try:
            raw = self.client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
            value = json.loads(raw)
        except Exception as exc:
            if default is None or not _is_not_found(exc):
                raise
            value = default
        self._cache[name] = (time.monotonic(), value)
        return value

    def put_json(self, name: str, value: Any, *, secure: bool = True) -> None:
        self.client.put_parameter(
            Name=name,
            Value=json.dumps(value, separators=(",", ":")),
            Type="SecureString" if secure else "String",
            Overwrite=True,
            Tier="Intelligent-Tiering",
        )
        self._cache[name] = (time.monotonic(), value)


def _is_not_found(exc: Exception) -> bool:
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
    return code == "ParameterNotFound" or type(exc).__name__ == "ParameterNotFound"


def incident_settings(store: ParamStore) -> dict[str, Any]:
    return {**DEFAULT_INCIDENT_SETTINGS, **(store.get_json(INCIDENT_SETTINGS, default={}) or {})}
