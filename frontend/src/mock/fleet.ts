import type { Maintenance, MachineKpi, Metrics, PartStock, Technician } from "../api/types";

export const OPERATION_ID = "MINA-NORTE";

export const ZONES: Record<string, { lat: number; lon: number; name: string }> = {
  Z1: { lat: -23.5931, lon: -69.9812, name: "Rajo norte" },
  Z2: { lat: -23.6078, lon: -69.9905, name: "Botadero" },
  Z3: { lat: -23.6009, lon: -69.9775, name: "Rajo sur" },
  Z4: { lat: -23.6122, lon: -69.9701, name: "Chancado" },
};

export const FUEL_BATCH_BY_ZONE: Record<string, string> = { Z1: "FB-1118", Z2: "FB-1118", Z3: "FB-1120", Z4: "FB-1119" };

interface ModelSpec {
  machineType: string;
  normal: Omit<Metrics, "engineHours" | "fuelLevelPct">;
  intervalHours: number;
}

export const MODELS: Record<string, ModelSpec> = {
  "EXC-320": {
    machineType: "EXCAVATOR",
    normal: { temperatureC: 78, vibrationMmS: 4.2, pressureBar: 210, fuelRateLph: 31, loadTons: 18, speedKmh: 4, rpm: 1800 },
    intervalHours: 500,
  },
  "HT-793": {
    machineType: "HAUL_TRUCK",
    normal: { temperatureC: 85, vibrationMmS: 3.1, pressureBar: 180, fuelRateLph: 95, loadTons: 220, speedKmh: 28, rpm: 1700 },
    intervalHours: 500,
  },
  "LD-980": {
    machineType: "LOADER",
    normal: { temperatureC: 80, vibrationMmS: 3.8, pressureBar: 195, fuelRateLph: 42, loadTons: 12, speedKmh: 10, rpm: 1900 },
    intervalHours: 400,
  },
  "BD-850": {
    machineType: "BULLDOZER",
    normal: { temperatureC: 82, vibrationMmS: 5.0, pressureBar: 200, fuelRateLph: 48, loadTons: 0, speedKmh: 6, rpm: 1850 },
    intervalHours: 450,
  },
  "DR-400": {
    machineType: "DRILL",
    normal: { temperatureC: 75, vibrationMmS: 6.0, pressureBar: 240, fuelRateLph: 36, loadTons: 0, speedKmh: 0, rpm: 2100 },
    intervalHours: 400,
  },
};

export interface MachineSeed {
  machineId: string;
  model: string;
  zoneId: string;
  engineHours: number;
  lastMaintenanceHours: number;
  idle?: boolean;
}

export const MACHINES: MachineSeed[] = [
  { machineId: "EXC-0042", model: "EXC-320", zoneId: "Z3", engineHours: 12450, lastMaintenanceHours: 12000 },
  { machineId: "EXC-0043", model: "EXC-320", zoneId: "Z1", engineHours: 9800, lastMaintenanceHours: 9500 },
  { machineId: "EXC-0051", model: "EXC-320", zoneId: "Z3", engineHours: 15020, lastMaintenanceHours: 14550 },
  { machineId: "TRK-0101", model: "HT-793", zoneId: "Z1", engineHours: 20110, lastMaintenanceHours: 20000 },
  { machineId: "TRK-0102", model: "HT-793", zoneId: "Z2", engineHours: 18760, lastMaintenanceHours: 18300 },
  { machineId: "TRK-0103", model: "HT-793", zoneId: "Z3", engineHours: 21540, lastMaintenanceHours: 21200 },
  { machineId: "TRK-0104", model: "HT-793", zoneId: "Z2", engineHours: 7300, lastMaintenanceHours: 7000, idle: true },
  { machineId: "TRK-0107", model: "HT-793", zoneId: "Z3", engineHours: 8320, lastMaintenanceHours: 8000 },
  { machineId: "LDR-0011", model: "LD-980", zoneId: "Z4", engineHours: 11200, lastMaintenanceHours: 10900 },
  { machineId: "LDR-0012", model: "LD-980", zoneId: "Z3", engineHours: 6400, lastMaintenanceHours: 6100 },
  { machineId: "DOZ-0007", model: "BD-850", zoneId: "Z2", engineHours: 13350, lastMaintenanceHours: 12950 },
  { machineId: "DRL-0003", model: "DR-400", zoneId: "Z4", engineHours: 5600, lastMaintenanceHours: 5250, idle: true },
];

export const TECHNICIANS: Technician[] = [
  { techId: "T-03", name: "Rosa Quispe", specialties: ["BEARING", "DRIVETRAIN"], availability: "AVAILABLE", lat: -23.5955, lon: -69.9840, queue: 0 },
  { techId: "T-07", name: "Jorge Mamani", specialties: ["HYDRAULIC_SYSTEM", "BEARING"], availability: "AVAILABLE", lat: -23.6030, lon: -69.9790, queue: 0 },
  { techId: "T-09", name: "Lucía Torres", specialties: ["ENGINE", "FUEL_SYSTEM"], availability: "AVAILABLE", lat: -23.6100, lon: -69.9720, queue: 0 },
  { techId: "T-12", name: "Carlos Rojas", specialties: ["COOLING_SYSTEM", "ENGINE"], availability: "AVAILABLE", lat: -23.6070, lon: -69.9900, queue: 0 },
  { techId: "T-15", name: "Ana Flores", specialties: ["HYDRAULIC_SYSTEM"], availability: "OFF", lat: -23.6000, lon: -69.9800, queue: 0 },
];

export const PART_FOR_COMPONENT: Record<string, string> = {
  BEARING: "BRG-SPH-22",
  COOLING_SYSTEM: "CLT-RAD-05",
  HYDRAULIC_SYSTEM: "FLT-HID-01",
  FUEL_SYSTEM: "FLT-FUE-02",
  ENGINE: "INJ-DSL-07",
};

export const PARTS: PartStock[] = [
  { partId: "FLT-HID-01", name: "Filtro hidráulico", component: "HYDRAULIC_SYSTEM", warehouses: [
    { warehouseId: "WH-NORTE", qty: 6, reserved: 0, reorderPoint: 4, forecastNext4Weeks: [3, 4, 3, 5] },
    { warehouseId: "WH-SUR", qty: 2, reserved: 0, reorderPoint: 3, forecastNext4Weeks: [2, 2, 3, 2] } ] },
  { partId: "BRG-SPH-22", name: "Rodamiento esférico", component: "BEARING", warehouses: [
    { warehouseId: "WH-NORTE", qty: 2, reserved: 0, reorderPoint: 2, forecastNext4Weeks: [1, 1, 2, 1] },
    { warehouseId: "WH-SUR", qty: 1, reserved: 0, reorderPoint: 2, forecastNext4Weeks: [1, 0, 1, 1] } ] },
  { partId: "FLT-FUE-02", name: "Filtro de combustible", component: "FUEL_SYSTEM", warehouses: [
    { warehouseId: "WH-NORTE", qty: 14, reserved: 0, reorderPoint: 8, forecastNext4Weeks: [5, 6, 5, 7] },
    { warehouseId: "WH-SUR", qty: 9, reserved: 0, reorderPoint: 6, forecastNext4Weeks: [4, 3, 4, 4] } ] },
  { partId: "CLT-RAD-05", name: "Kit de radiador", component: "COOLING_SYSTEM", warehouses: [
    { warehouseId: "WH-NORTE", qty: 3, reserved: 0, reorderPoint: 1, forecastNext4Weeks: [0, 1, 0, 0] },
    { warehouseId: "WH-SUR", qty: 4, reserved: 0, reorderPoint: 1, forecastNext4Weeks: [0, 0, 1, 0] } ] },
  { partId: "INJ-DSL-07", name: "Inyector diésel", component: "ENGINE", warehouses: [
    { warehouseId: "WH-NORTE", qty: 5, reserved: 0, reorderPoint: 4, forecastNext4Weeks: [2, 9, 1, 2] },
    { warehouseId: "WH-SUR", qty: 4, reserved: 0, reorderPoint: 4, forecastNext4Weeks: [1, 2, 2, 1] } ] },
];

export const WAREHOUSE_BY_ZONE: Record<string, string> = { Z1: "WH-NORTE", Z2: "WH-SUR", Z3: "WH-NORTE", Z4: "WH-SUR" };

export function seedMaintenance(machineId: string, lastMaintenanceHours: number, now: Date): Maintenance[] {
  const day = 86_400_000;
  return [
    {
      maintenanceId: `MNT-${machineId.slice(-4)}-2`,
      date: new Date(now.getTime() - 21 * day).toISOString(),
      type: "PREVENTIVE",
      engineHours: lastMaintenanceHours,
      partsReplaced: ["FLT-HID-01", "FLT-FUE-02"],
      notes: "Servicio preventivo según horas de uso",
    },
    {
      maintenanceId: `MNT-${machineId.slice(-4)}-1`,
      date: new Date(now.getTime() - 64 * day).toISOString(),
      type: "CORRECTIVE",
      engineHours: lastMaintenanceHours - 420,
      partsReplaced: ["BRG-SPH-22"],
      notes: "Cambio de rodamiento por vibración alta",
    },
  ];
}

/** Deterministic daily KPIs with a few planted outliers for the demo (P6, P7). */
export function seedKpis(date: string): MachineKpi[] {
  const outliers: Record<string, Partial<MachineKpi>> = {
    "TRK-0104": { utilizationPct: 18, idleHours: 17.5, costPerHour: 410 },
    "DRL-0003": { utilizationPct: 22, idleHours: 16.8, costPerHour: 360 },
    "TRK-0101": { utilizationPct: 96, overuseHours: 4.5 },
    "EXC-0051": { fuelLitersPerTon: 2.4, fuelAnomaly: true, costPerHour: 390 },
    "LDR-0011": { fuelLitersPerTon: 4.6, fuelAnomaly: true },
  };
  const fleetFuel: Record<string, number> = { "EXC-320": 1.7, "HT-793": 0.45, "LD-980": 3.4, "BD-850": 0, "DR-400": 0 };
  return MACHINES.map((m, i) => {
    const base = fleetFuel[m.model];
    const utilization = 62 + ((i * 7) % 25);
    return {
      machineId: m.machineId,
      model: m.model,
      date,
      utilizationPct: utilization,
      idleHours: +(24 * (1 - utilization / 100)).toFixed(1),
      overuseHours: 0,
      tonsMoved: base ? Math.round(1800 + i * 210) : 0,
      fuelLitersPerTon: +(base * (0.95 + (i % 3) * 0.04)).toFixed(2),
      fuelLitersPerTonFleet: base,
      costPerHour: 180 + ((i * 23) % 90),
      fuelAnomaly: false,
      ...outliers[m.machineId],
    };
  });
}
