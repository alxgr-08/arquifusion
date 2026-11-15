// Response shapes for contracts/api.md. Proposed by Rol 3 until Rol 2 publishes contracts/examples/api/.

export type Severity = "OK" | "WATCH" | "WARNING" | "CRITICAL";
export type MachineStatus = "OPERATING" | "IDLE" | "STOPPED" | "MAINTENANCE" | "OFFLINE";
export type Role = "OPERATOR" | "TECHNICIAN" | "MAINT_LEAD" | "SUPERVISOR" | "MANAGER";
export type Trend = "RISING" | "FALLING" | "STABLE";
export type IncidentState = "OPEN" | "ASSIGNED" | "IN_PROGRESS" | "RESOLVED";
export type FaultType = "BEARING_WEAR" | "OVERHEATING" | "HYDRAULIC_LEAK" | "FUEL_BATCH_ZONE";

export interface Metrics {
  engineHours: number | null;
  temperatureC: number | null;
  vibrationMmS: number | null;
  pressureBar: number | null;
  fuelRateLph: number | null;
  fuelLevelPct: number | null;
  loadTons: number | null;
  speedKmh: number | null;
  rpm: number | null;
}

export interface Machine {
  machineId: string;
  model: string;
  machineType: string;
  operationId: string;
  zoneId: string;
  lat: number;
  lon: number;
  status: MachineStatus;
  healthScore: number;
  severity: Severity;
  lastSeen: string;
  engineHours: number;
  lastMaintenanceHours: number;
  maintenanceIntervalHours: number;
  openIncidentId?: string;
}

export interface Reading {
  timestamp: string;
  healthScore: number;
  severity: Severity;
  metrics: Metrics;
}

export interface Signal {
  metric: string;
  value: number;
  baseline: number;
  zScore: number;
  trend: Trend;
}

export interface AiDiagnosis {
  source: "BEDROCK" | "RULES";
  probableCause: string;
  component: string;
  confidence: "LOW" | "MEDIUM" | "HIGH";
  recommendedAction: string;
  stopMachine: boolean;
  estimatedRepairMinutes: number;
  zoneCause?: string;
}

export interface TimelineEvent {
  at: string;
  event: "DETECTED" | "ASSIGNED" | "ARRIVED" | "RESOLVED" | "ESCALATED";
  tech?: string;
}

export interface Incident {
  incidentId: string;
  machineId: string;
  operationId: string;
  zoneId: string;
  severity: Severity;
  state: IncidentState;
  createdAt: string;
  healthScore: number;
  signals: Signal[];
  aiDiagnosis: AiDiagnosis;
  messages: Record<Role, string>;
  assignedTech?: string;
  etaMinutes?: number;
  partsReserved: { partId: string; warehouse: string; qty: number }[];
  timeline: TimelineEvent[];
  downtimeMinutes?: number;
  recurrenceOf?: string;
  zoneIncidentId?: string;
  correlatedIncidents: string[];
}

export interface Maintenance {
  maintenanceId: string;
  date: string;
  type: "PREVENTIVE" | "CORRECTIVE";
  engineHours: number;
  partsReplaced: string[];
  notes: string;
}

export interface Technician {
  techId: string;
  name: string;
  specialties: string[];
  availability: "AVAILABLE" | "BUSY" | "OFF";
  lat: number;
  lon: number;
  queue: number;
}

export interface WarehouseStock {
  warehouseId: string;
  qty: number;
  reserved: number;
  reorderPoint: number;
  forecastNext4Weeks: number[];
}

export interface PartStock {
  partId: string;
  name: string;
  component: string;
  warehouses: WarehouseStock[];
}

export interface MachineKpi {
  machineId: string;
  model: string;
  date: string;
  utilizationPct: number;
  idleHours: number;
  overuseHours: number;
  tonsMoved: number;
  fuelLitersPerTon: number;
  fuelLitersPerTonFleet: number;
  costPerHour: number;
  fuelAnomaly: boolean;
}

export interface ApiError {
  error: { code: string; message: string };
}
