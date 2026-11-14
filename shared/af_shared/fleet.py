"""Demo fleet model (contracts/fleet-config.schema.json) and small geo helpers. Owner: Rol 2."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

METRIC_NAMES = ("temperatureC", "vibrationMmS", "pressureBar", "fuelRateLph", "loadTons", "speedKmh", "rpm")


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Zone(_Camel):
    zone_id: str = Field(alias="zoneId")
    name: str
    lat: float
    lon: float
    radius_km: float = Field(alias="radiusKm")


class Warehouse(_Camel):
    warehouse_id: str = Field(alias="warehouseId")
    name: str
    lat: float
    lon: float


class Stat(_Camel):
    mean: float
    std: float


class MachineModel(_Camel):
    model: str
    machine_type: str = Field(alias="machineType")
    nominal_load_tons: float = Field(alias="nominalLoadTons")
    fuel_tank_l: float = Field(alias="fuelTankL")
    maintenance_interval_hours: float = Field(alias="maintenanceIntervalHours")
    cost_per_hour_usd: float = Field(alias="costPerHourUsd")
    metrics: dict[str, Stat]


class Machine(_Camel):
    machine_id: str = Field(alias="machineId")
    model: str
    zone_id: str = Field(alias="zoneId")
    engine_hours: float = Field(alias="engineHours")
    last_maintenance_hours: float = Field(alias="lastMaintenanceHours")
    utilization: float
    fuel_batch_id: str = Field(alias="fuelBatchId")


class Technician(_Camel):
    tech_id: str = Field(alias="techId")
    name: str
    specialties: list[str]
    lat: float
    lon: float


class Part(_Camel):
    part_id: str = Field(alias="partId")
    name: str
    unit_cost_usd: float = Field(alias="unitCostUsd")
    lead_time_days: int = Field(alias="leadTimeDays")
    stock: dict[str, int]


class PartQty(_Camel):
    part_id: str = Field(alias="partId")
    qty: int


class FailureMode(_Camel):
    mode: str
    label: str
    metrics: list[str]
    specialty: str
    parts: list[PartQty]


class FaultSpec(_Camel):
    sigma: dict[str, float] = Field(default_factory=dict)
    null_metrics: list[str] = Field(default_factory=list, alias="nullMetrics")
    status: str | None = None
    fuel_batch_id: str | None = Field(default=None, alias="fuelBatchId")


class Demo(_Camel):
    fast_interval_seconds: int = Field(alias="fastIntervalSeconds")
    ramp_seconds: int = Field(alias="rampSeconds")


class FleetConfig(_Camel):
    schema_version: str = Field(alias="schemaVersion")
    operation_id: str = Field(alias="operationId")
    reference_time: str = Field(alias="referenceTime")
    zones: list[Zone] = Field(default_factory=list)
    warehouses: list[Warehouse] = Field(default_factory=list)
    models: list[MachineModel] = Field(default_factory=list)
    machines: list[Machine] = Field(default_factory=list)
    technicians: list[Technician] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)
    failure_modes: list[FailureMode] = Field(default_factory=list, alias="failureModes")
    fault_catalog: dict[str, FaultSpec] = Field(default_factory=dict, alias="faultCatalog")
    fault_intensity: dict[str, float] = Field(default_factory=dict, alias="faultIntensity")
    demo: Demo | None = None

    def model_of(self, model: str) -> MachineModel:
        return next(m for m in self.models if m.model == model)

    def machine(self, machine_id: str) -> Machine | None:
        return next((m for m in self.machines if m.machine_id == machine_id), None)

    def zone(self, zone_id: str) -> Zone:
        return next(z for z in self.zones if z.zone_id == zone_id)


# Subsets stored in SSM (standard parameters are limited to 4 KB, so the big catalog is split by consumer).
SIMULATOR_KEYS = (
    "schemaVersion",
    "operationId",
    "referenceTime",
    "zones",
    "models",
    "machines",
    "faultCatalog",
    "faultIntensity",
    "demo",
)
INCIDENT_KEYS = ("schemaVersion", "operationId", "referenceTime", "warehouses", "failureModes")


def subset(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: raw[k] for k in keys if k in raw}


def load_file(path: str | Path) -> FleetConfig:
    return FleetConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
