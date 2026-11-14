import type { MachineStatus, Role, Severity } from "../api/types";

export function severityFromHealth(health: number): Severity {
  if (health < 40) return "CRITICAL";
  if (health < 60) return "WARNING";
  if (health < 80) return "WATCH";
  return "OK";
}

export const SEVERITY_RANK: Record<Severity, number> = { CRITICAL: 1, WARNING: 2, WATCH: 3, OK: 4 };

export const SEVERITY_COLOR: Record<Severity, string> = {
  OK: "#2e9e5b",
  WATCH: "#d4a017",
  WARNING: "#e8710a",
  CRITICAL: "#d93025",
};

export const STATUS_LABEL: Record<MachineStatus, string> = {
  OPERATING: "Operando",
  IDLE: "Inactiva",
  STOPPED: "Detenida",
  MAINTENANCE: "Mantenimiento",
  OFFLINE: "Sin señal",
};

export const ROLE_LABEL: Record<Role, string> = {
  OPERATOR: "Operador",
  TECHNICIAN: "Técnico",
  MAINT_LEAD: "Jefe de mantenimiento",
  SUPERVISOR: "Supervisor",
  MANAGER: "Gerencia",
};

export const METRIC_LABEL: Record<string, string> = {
  temperatureC: "Temperatura (°C)",
  vibrationMmS: "Vibración (mm/s)",
  pressureBar: "Presión (bar)",
  fuelRateLph: "Combustible (L/h)",
  rpm: "RPM",
  loadTons: "Carga (t)",
};

/** Great-circle distance in km. */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

/** Hours until next preventive maintenance, adjusted by usage factor (guide 5.2). */
export function hoursToMaintenance(engineHours: number, lastMaintenanceHours: number, intervalHours: number, usageFactor = 1) {
  return intervalHours / usageFactor - (engineHours - lastMaintenanceHours);
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("es-PE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
