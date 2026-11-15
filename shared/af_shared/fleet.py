"""Demo fleet (contracts/examples/fleet-config.json, owner of the data: Rol 3) as typed objects. Helpers: Rol 2.

The same file feeds the frontend mock, the synthetic history (BigQuery) and, through SSM /af/sim/fleet-config,
the simulator, the seed script and the incident flow.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

METRIC_NAMES = ("temperatureC", "vibrationMmS", "pressureBar", "fuelRateLph", "loadTons", "speedKmh", "rpm")
# Relative spread of each metric around its normal value (same jitter the frontend mock uses).
SPREAD_PCT = {
    "temperatureC": 0.03,
    "vibrationMmS": 0.10,
    "pressureBar": 0.03,
    "fuelRateLph": 0.05,
    "loadTons": 0.10,
    "speedKmh": 0.20,
    "rpm": 0.03,
}


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Zone(_Camel):
    name: str
    lat: float
    lon: float
    fuel_batch_id: str | None = Field(default=None, alias="fuelBatchId")


class MachineModel(_Camel):
    machine_type: str = Field(alias="machineType")
    interval_hours: float = Field(alias="intervalHours")
    nominal_load_tons: float = Field(default=0, alias="nominalLoadTons")
    normal: dict[str, float]

    def normal_value(self, metric: str) -> float:
        if metric == "loadTons":
            return float(self.normal.get("loadTons", self.nominal_load_tons))
        return float(self.normal.get(metric, 0.0))

    def std(self, metric: str) -> float:
        """Standard deviation of the simulator noise: uniform +-pct/2 has std = pct * value / sqrt(12)."""
        return abs(self.normal_value(metric)) * SPREAD_PCT.get(metric, 0.05) / math.sqrt(12)


class Machine(_Camel):
    machine_id: str = Field(alias="machineId")
    model: str
    zone_id: str = Field(alias="zoneId")
    engine_hours: float = Field(alias="engineHours")
    last_maintenance_hours: float = Field(alias="lastMaintenanceHours")
    utilization: float = 0.7


class Technician(_Camel):
    tech_id: str = Field(alias="techId")
    name: str
    specialties: list[str]
    lat: float
    lon: float


class Warehouse(_Camel):
    zones: list[str]


class Part(_Camel):
    part_id: str = Field(alias="partId")
    name: str
    component: str
    unit_cost: float = Field(default=0, alias="unitCost")
    stock: dict[str, int]
    daily_demand: float = Field(default=0, alias="dailyDemand")


class FleetConfig(_Camel):
    schema_version: str = Field(alias="schemaVersion")
    operation_id: str = Field(alias="operationId")
    zones: dict[str, Zone]
    models: dict[str, MachineModel]
    machines: list[Machine]
    technicians: list[Technician] = Field(default_factory=list)
    warehouses: dict[str, Warehouse] = Field(default_factory=dict)
    parts: list[Part] = Field(default_factory=list)

    def model_of(self, model: str) -> MachineModel:
        return self.models[model]

    def machine(self, machine_id: str) -> Machine | None:
        return next((m for m in self.machines if m.machine_id == machine_id), None)

    def zone(self, zone_id: str) -> Zone:
        return self.zones[zone_id]

    def warehouses_for_zone(self, zone_id: str) -> list[str]:
        """The warehouse that serves the zone first, then the others as fallback."""
        own = [w for w, wh in self.warehouses.items() if zone_id in wh.zones]
        return own + sorted(w for w in self.warehouses if w not in own)

    def parts_for_component(self, component: str) -> list[Part]:
        return [p for p in self.parts if p.component == component]


def load_file(path: str | Path) -> FleetConfig:
    return FleetConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
