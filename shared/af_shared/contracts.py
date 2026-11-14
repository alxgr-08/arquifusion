"""Pydantic models mirroring contracts/*.schema.json (v1.0)."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MachineStatus(StrEnum):
    OPERATING = "OPERATING"
    IDLE = "IDLE"
    STOPPED = "STOPPED"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"  # assigned by offline-detector, never sent by machines


class Severity(StrEnum):
    OK = "OK"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Trend(StrEnum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(_Strict):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    zone_id: str = Field(alias="zoneId")


class Metrics(_Strict):
    engine_hours: float | None = Field(alias="engineHours", ge=0)
    temperature_c: float | None = Field(alias="temperatureC")
    vibration_mm_s: float | None = Field(alias="vibrationMmS", ge=0)
    pressure_bar: float | None = Field(alias="pressureBar", ge=0)
    fuel_rate_lph: float | None = Field(alias="fuelRateLph", ge=0)
    fuel_level_pct: float | None = Field(alias="fuelLevelPct", ge=0, le=100)
    load_tons: float | None = Field(alias="loadTons", ge=0)
    speed_kmh: float | None = Field(alias="speedKmh", ge=0)
    rpm: float | None = Field(ge=0)


class TelemetryContext(BaseModel):
    fuel_batch_id: str | None = Field(default=None, alias="fuelBatchId")
    ambient_temp_c: float | None = Field(default=None, alias="ambientTempC")


class Telemetry(_Strict):
    schema_version: Literal["1.0"] = Field(alias="schemaVersion")
    message_id: UUID = Field(alias="messageId")
    machine_id: str = Field(alias="machineId", pattern=r"^[A-Z]{2,5}-[0-9]{4}$")
    operation_id: str = Field(alias="operationId")
    model: str
    machine_type: str = Field(alias="machineType")
    timestamp: datetime
    location: Location
    status: Literal["OPERATING", "IDLE", "STOPPED", "MAINTENANCE"]
    metrics: Metrics
    context: TelemetryContext | None = None


class Signal(_Strict):
    metric: str
    value: float
    baseline: float
    z_score: float = Field(alias="zScore")
    trend: Trend


class AnomalyDetail(_Strict):
    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    machine_id: str = Field(alias="machineId")
    operation_id: str = Field(alias="operationId")
    zone_id: str = Field(alias="zoneId")
    model: str
    detected_at: datetime = Field(alias="detectedAt")
    health_score: float = Field(alias="healthScore", ge=0, le=100)
    severity: Severity
    signals: list[Signal] = Field(min_length=1)
    compared_against: list[Literal["OWN_HISTORY", "MODEL_FLEET"]] = Field(alias="comparedAgainst", min_length=1)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnomalyDetected(BaseModel):
    source: Literal["arquifusion.health"] = "arquifusion.health"
    detail_type: Literal["AnomalyDetected"] = Field(default="AnomalyDetected", alias="detail-type")
    detail: AnomalyDetail

    model_config = ConfigDict(populate_by_name=True)


# --- ai-diagnosis (contracts/ai-diagnosis-*.schema.json) -------------------------------------

Component = Literal[
    "BEARING", "COOLING_SYSTEM", "HYDRAULIC_SYSTEM", "FUEL_SYSTEM", "ENGINE", "DRIVETRAIN", "SENSOR", "UNKNOWN"
]
Confidence = Literal["LOW", "MEDIUM", "HIGH"]


class _Camel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MachineSnapshot(_Camel):
    status: str | None = None
    engine_hours: float | None = Field(default=None, alias="engineHours")
    last_maintenance_hours: float | None = Field(default=None, alias="lastMaintenanceHours")


class HistoryEntry(_Camel):
    type: Literal["INCIDENT", "MAINTENANCE"]
    at: datetime
    summary: str
    parts_replaced: list[str] | None = Field(default=None, alias="partsReplaced")


class ZoneMachine(_Camel):
    machine_id: str = Field(alias="machineId")
    model: str
    detected_at: datetime = Field(alias="detectedAt")
    severity: Severity
    top_metric: str | None = Field(default=None, alias="topMetric")
    fuel_batch_id: str | None = Field(default=None, alias="fuelBatchId")


class DiagnosisRequest(_Camel):
    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    incident_id: str | None = Field(default=None, alias="incidentId")
    anomaly: AnomalyDetail
    machine: MachineSnapshot | None = None
    context: TelemetryContext | None = None
    history: list[HistoryEntry] = Field(default_factory=list, max_length=10)
    zone_correlation: list[ZoneMachine] = Field(default_factory=list, alias="zoneCorrelation")


class Diagnosis(_Camel):
    probable_cause: str = Field(alias="probableCause")
    component: Component
    confidence: Confidence
    recommended_action: str = Field(alias="recommendedAction")
    stop_machine: bool = Field(alias="stopMachine")
    estimated_repair_minutes: int = Field(alias="estimatedRepairMinutes", ge=0)


class ZoneAnalysis(_Camel):
    is_zone_incident: bool = Field(alias="isZoneIncident")
    common_cause: str = Field(alias="commonCause")
    evidence: list[str]


class RoleMessages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    OPERATOR: str
    TECHNICIAN: str
    MAINT_LEAD: str
    SUPERVISOR: str
    MANAGER: str


class DiagnosisResult(_Camel):
    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    source: Literal["BEDROCK", "RULES"]
    model_id: str | None = Field(default=None, alias="modelId")
    diagnosis: Diagnosis
    zone_analysis: ZoneAnalysis | None = Field(alias="zoneAnalysis")
    messages: RoleMessages
