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


# --- ai-diagnosis (PROPOSAL v1.0: contracts/ai-diagnosis.*.schema.json) ---------------------------------------
class PartRequest(_Strict):
    part_id: str = Field(alias="partId")
    qty: int = Field(ge=1)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RoleMessages(_Strict):
    operator: str = Field(alias="OPERATOR")
    technician: str = Field(alias="TECHNICIAN")
    maint_lead: str = Field(alias="MAINT_LEAD")
    supervisor: str = Field(alias="SUPERVISOR")
    manager: str = Field(alias="MANAGER")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AiDiagnosisResponse(_Strict):
    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    source: Literal["BEDROCK", "GEMINI", "RULES"]
    failure_mode: str = Field(alias="failureMode")
    diagnosis: str = Field(max_length=400)
    confidence: float = Field(ge=0, le=1)
    recommended_parts: list[PartRequest] = Field(alias="recommendedParts")
    required_specialty: Literal["HYDRAULIC", "MECHANICAL", "ENGINE", "ELECTRICAL"] = Field(alias="requiredSpecialty")
    operator_action: Literal["STOP", "CONTINUE_WITH_CAUTION", "CONTINUE"] = Field(alias="operatorAction")
    messages: RoleMessages
    zone_hypothesis: str | None = Field(default=None, alias="zoneHypothesis")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
