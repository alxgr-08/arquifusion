import type { FleetApi } from "../api/client";
import type {
  AiDiagnosis,
  FaultType,
  Incident,
  Machine,
  MachineStatus,
  Maintenance,
  Metrics,
  Reading,
  Role,
  Signal,
  Technician,
} from "../api/types";
import { haversineKm, severityFromHealth, SEVERITY_RANK } from "../lib/domain";
import {
  FUEL_BATCH_BY_ZONE,
  MACHINES,
  MODELS,
  OPERATION_ID,
  PART_FOR_COMPONENT,
  PARTS,
  seedKpis,
  seedMaintenance,
  TECHNICIANS,
  WAREHOUSE_BY_ZONE,
  ZONES,
} from "./fleet";

const MINUTE = 60_000;
const HISTORY_MINUTES = 120;
const FAULT_STEP = 0.1;
const TECH_SPEED_KMH = 25;

interface Fault {
  type: FaultType;
  progress: number;
}

interface FaultProfile {
  penalty: number;
  component: string;
  apply: (m: Metrics, p: number) => void;
  cause: string;
  action: string;
  minutes: number;
}

const FAULTS: Record<FaultType, FaultProfile> = {
  BEARING_WEAR: {
    penalty: 78,
    component: "BEARING",
    apply: (m, p) => {
      m.temperatureC! += 15 * p;
      m.vibrationMmS! += 4.2 * p;
    },
    cause: "Probable desgaste de rodamiento: temperatura y vibración suben juntas.",
    action: "Detener, inspeccionar rodamientos y lubricación; reemplazar si hay holgura.",
    minutes: 120,
  },
  OVERHEATING: {
    penalty: 50,
    component: "COOLING_SYSTEM",
    apply: (m, p) => {
      m.temperatureC! += 22 * p;
    },
    cause: "Probable falla de refrigeración: la temperatura sube sin otra causa mecánica.",
    action: "Revisar refrigerante, radiador y ventilador.",
    minutes: 60,
  },
  HYDRAULIC_LEAK: {
    penalty: 62,
    component: "HYDRAULIC_SYSTEM",
    apply: (m, p) => {
      m.pressureBar! -= 55 * p;
    },
    cause: "Probable fuga hidráulica: la presión cae por debajo de lo normal.",
    action: "Revisar mangueras, sellos y nivel de aceite; cambiar filtro hidráulico.",
    minutes: 90,
  },
  FUEL_BATCH_ZONE: {
    penalty: 50,
    component: "FUEL_SYSTEM",
    apply: (m, p) => {
      m.fuelRateLph! *= 1 + 0.35 * p;
      m.temperatureC! += 5 * p;
    },
    cause: "Consumo de combustible anormal con leve alza de temperatura: posible combustible contaminado.",
    action: "Drenar y cambiar filtro de combustible; tomar muestra del lote.",
    minutes: 60,
  },
};

/** Small deterministic PRNG so tests and demos are reproducible. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clone = <T>(v: T): T => structuredClone(v);

export class MockFleetApi implements FleetApi {
  private now: number;
  private readonly rand: () => number;
  private readonly machines = new Map<string, Machine>();
  private readonly readings = new Map<string, Reading[]>();
  private readonly faults = new Map<string, Fault>();
  private readonly incidents: Incident[] = [];
  private readonly maintenance = new Map<string, Maintenance[]>();
  private readonly technicians: Technician[] = clone(TECHNICIANS);
  private readonly parts = clone(PARTS);
  private seq = 0;

  constructor(options: { startTime?: Date; seed?: number } = {}) {
    this.now = (options.startTime ?? new Date()).getTime() - HISTORY_MINUTES * MINUTE;
    this.rand = mulberry32(options.seed ?? 42);
    for (const seed of MACHINES) {
      const zone = ZONES[seed.zoneId];
      this.machines.set(seed.machineId, {
        machineId: seed.machineId,
        model: seed.model,
        machineType: MODELS[seed.model].machineType,
        operationId: OPERATION_ID,
        zoneId: seed.zoneId,
        lat: zone.lat + (this.rand() - 0.5) * 0.006,
        lon: zone.lon + (this.rand() - 0.5) * 0.006,
        status: seed.idle ? "IDLE" : "OPERATING",
        healthScore: 100,
        severity: "OK",
        lastSeen: new Date(this.now).toISOString(),
        engineHours: seed.engineHours,
        lastMaintenanceHours: seed.lastMaintenanceHours,
        maintenanceIntervalHours: MODELS[seed.model].intervalHours,
      });
      this.readings.set(seed.machineId, []);
      this.maintenance.set(seed.machineId, seedMaintenance(seed.machineId, seed.lastMaintenanceHours, new Date(this.now)));
    }
    for (let i = 0; i < HISTORY_MINUTES; i++) this.tick();
  }

  /** Advance the simulated clock by one minute: every machine reports one reading. */
  tick(): void {
    this.now += MINUTE;
    for (const machine of this.machines.values()) {
      const fault = this.faults.get(machine.machineId);
      if (fault) fault.progress = Math.min(1, fault.progress + FAULT_STEP);

      const metrics = this.sampleMetrics(machine, fault);
      const penalty = fault ? FAULTS[fault.type].penalty * fault.progress : 0;
      const health = Math.round(Math.max(0, 100 - penalty - this.rand() * 4) * 10) / 10;
      const previous = machine.severity;

      machine.healthScore = health;
      machine.severity = severityFromHealth(health);
      machine.lastSeen = new Date(this.now).toISOString();
      if (machine.status === "OPERATING") machine.engineHours = +(machine.engineHours + 1 / 60).toFixed(2);

      const history = this.readings.get(machine.machineId)!;
      history.push({ timestamp: machine.lastSeen, healthScore: health, severity: machine.severity, metrics });
      if (history.length > HISTORY_MINUTES * 1.5) history.shift();

      const escalated = SEVERITY_RANK[machine.severity] < SEVERITY_RANK[previous];
      if (escalated && (machine.severity === "WARNING" || machine.severity === "CRITICAL") && fault) {
        this.raiseIncident(machine, fault, metrics);
      }
    }
  }

  private sampleMetrics(machine: Machine, fault?: Fault): Metrics {
    const n = MODELS[machine.model].normal;
    const jitter = (v: number | null, pct: number) => (v === null ? null : +(v * (1 + (this.rand() - 0.5) * pct)).toFixed(2));
    const idle = machine.status !== "OPERATING";
    const metrics: Metrics = {
      engineHours: machine.engineHours,
      temperatureC: jitter(idle ? n.temperatureC! - 20 : n.temperatureC, 0.03),
      vibrationMmS: jitter(idle ? 0.8 : n.vibrationMmS, 0.1),
      pressureBar: jitter(n.pressureBar, 0.03),
      fuelRateLph: jitter(idle ? n.fuelRateLph! * 0.15 : n.fuelRateLph, 0.05),
      fuelLevelPct: 40 + Math.round(this.rand() * 50),
      loadTons: idle ? 0 : jitter(n.loadTons, 0.1),
      speedKmh: idle ? 0 : jitter(n.speedKmh, 0.2),
      rpm: jitter(idle ? 750 : n.rpm, 0.03),
    };
    if (fault) FAULTS[fault.type].apply(metrics, fault.progress);
    return metrics;
  }

  private signals(machine: Machine, metrics: Metrics): Signal[] {
    const n = MODELS[machine.model].normal as Record<string, number | null>;
    const std: Record<string, number> = { temperatureC: 2.5, vibrationMmS: 0.6, pressureBar: 6, fuelRateLph: 3 };
    return Object.keys(std)
      .map((metric) => {
        const value = metrics[metric as keyof Metrics] as number;
        const baseline = n[metric] as number;
        const z = +((value - baseline) / std[metric]).toFixed(1);
        return { metric, value, baseline, zScore: metric === "pressureBar" ? -z : z, trend: "RISING" as const };
      })
      .filter((s) => s.zScore >= 1.5)
      .sort((a, b) => b.zScore - a.zScore);
  }

  private raiseIncident(machine: Machine, fault: Fault, metrics: Metrics): void {
    const at = new Date(this.now).toISOString();
    const open = machine.openIncidentId && this.incidents.find((i) => i.incidentId === machine.openIncidentId);
    if (open) {
      open.severity = machine.severity;
      open.healthScore = machine.healthScore;
      open.signals = this.signals(machine, metrics);
      if (machine.severity === "CRITICAL") {
        machine.status = "STOPPED";
        open.aiDiagnosis.stopMachine = true;
        const tech = this.technicians.find((t) => t.techId === open.assignedTech);
        open.messages = this.messages(machine, open.aiDiagnosis, tech);
        open.timeline.push({ at, event: "ESCALATED" });
      }
      return;
    }

    const profile = FAULTS[fault.type];
    const incidentId = `INC-${(++this.seq).toString(16).padStart(4, "0")}`;
    const zoneOpen = this.incidents.filter(
      (i) => i.zoneId === machine.zoneId && i.state !== "RESOLVED" && this.now - Date.parse(i.createdAt) <= 120 * MINUTE,
    );
    const zoneIncident = zoneOpen.length >= 2;
    const batch = FUEL_BATCH_BY_ZONE[machine.zoneId];
    const zoneCause = zoneIncident
      ? fault.type === "FUEL_BATCH_ZONE"
        ? `Posible lote de combustible contaminado ${batch} (${zoneOpen.length + 1} máquinas en ${machine.zoneId}).`
        : `${zoneOpen.length + 1} máquinas con incidentes en ${machine.zoneId}: revisar condiciones de la zona.`
      : undefined;
    const stop = machine.severity === "CRITICAL";
    const diagnosis: AiDiagnosis = {
      source: "RULES",
      probableCause: profile.cause,
      component: profile.component,
      confidence: zoneIncident || fault.type === "BEARING_WEAR" ? "HIGH" : "MEDIUM",
      recommendedAction: profile.action,
      stopMachine: stop,
      estimatedRepairMinutes: profile.minutes,
      zoneCause,
    };

    const partId = PART_FOR_COMPONENT[profile.component];
    const partsReserved = partId ? this.reservePart(partId, WAREHOUSE_BY_ZONE[machine.zoneId]) : [];
    const tech = this.pickTechnician(machine, profile.component);

    const incident: Incident = {
      incidentId,
      machineId: machine.machineId,
      operationId: machine.operationId,
      zoneId: machine.zoneId,
      severity: machine.severity,
      state: tech ? "ASSIGNED" : "OPEN",
      createdAt: at,
      healthScore: machine.healthScore,
      signals: this.signals(machine, metrics),
      aiDiagnosis: diagnosis,
      messages: this.messages(machine, diagnosis, tech?.tech),
      assignedTech: tech?.tech.techId,
      etaMinutes: tech?.eta,
      partsReserved,
      timeline: [
        { at, event: "DETECTED" },
        ...(tech ? [{ at, event: "ASSIGNED" as const, tech: tech.tech.techId }] : []),
      ],
      correlatedIncidents: zoneIncident ? zoneOpen.map((i) => i.incidentId) : [],
      zoneIncidentId: zoneIncident ? `ZINC-${machine.zoneId}` : undefined,
      recurrenceOf: this.incidents.find((i) => i.machineId === machine.machineId && i.state === "RESOLVED")?.incidentId,
    };
    if (zoneIncident) {
      for (const other of zoneOpen) {
        other.zoneIncidentId = incident.zoneIncidentId;
        other.aiDiagnosis.zoneCause = zoneCause;
        if (!other.correlatedIncidents.includes(incidentId)) other.correlatedIncidents.push(incidentId);
        const otherMachine = this.machines.get(other.machineId)!;
        const otherTech = this.technicians.find((t) => t.techId === other.assignedTech);
        other.messages = this.messages(otherMachine, other.aiDiagnosis, otherTech);
      }
    }
    this.incidents.unshift(incident);
    machine.openIncidentId = incidentId;
    if (stop) machine.status = "STOPPED";
  }

  private reservePart(partId: string, warehouseId: string) {
    const stock = this.parts.find((p) => p.partId === partId)?.warehouses.find((w) => w.warehouseId === warehouseId);
    if (!stock || stock.qty - stock.reserved < 1) return [];
    stock.reserved += 1;
    return [{ partId, warehouse: warehouseId, qty: 1 }];
  }

  /** cost = ETA + 60 * (no specialty) + 15 * queue (guide 5.3). */
  private pickTechnician(machine: Machine, component: string) {
    const scored = this.technicians
      .filter((t) => t.availability === "AVAILABLE")
      .map((t) => {
        const eta = Math.max(5, Math.round((haversineKm(t.lat, t.lon, machine.lat, machine.lon) / TECH_SPEED_KMH) * 60));
        return { tech: t, eta, cost: eta + (t.specialties.includes(component) ? 0 : 60) + 15 * t.queue };
      })
      .sort((a, b) => a.cost - b.cost);
    const best = scored[0];
    if (best) {
      best.tech.availability = "BUSY";
      best.tech.queue += 1;
    }
    return best;
  }

  private messages(machine: Machine, d: AiDiagnosis, tech?: Technician): Record<Role, string> {
    const where = `${machine.machineId} (${ZONES[machine.zoneId].name}, ${machine.zoneId})`;
    const zone = d.zoneCause ? ` Incidente de zona: ${d.zoneCause}` : "";
    return {
      OPERATOR: d.stopMachine
        ? `Detén ${machine.machineId} de forma segura y espera al técnico.`
        : `Continúa con ${machine.machineId} reduciendo la carga; un técnico va en camino.`,
      TECHNICIAN: `${where}: ${d.probableCause} Acción: ${d.recommendedAction}`,
      MAINT_LEAD: `Incidente ${machine.severity} en ${where}. Componente ${d.component}, asignado a ${tech?.name ?? "nadie (sin técnicos libres)"}. ${d.estimatedRepairMinutes} min estimados.${zone}`,
      SUPERVISOR: `${machine.machineId} ${d.stopMachine ? "detenida" : "operando con restricción"} en ${machine.zoneId}.${zone}`,
      MANAGER: `${OPERATION_ID}: incidente ${machine.severity} en ${machine.machineId}, ~${d.estimatedRepairMinutes} min de inactividad.${zone}`,
    };
  }

  // --- FleetApi -----------------------------------------------------------------------------

  async listMachines(_op: string, status?: MachineStatus) {
    return clone([...this.machines.values()].filter((m) => !status || m.status === status));
  }

  async getMachine(id: string) {
    const machine = this.machines.get(id);
    if (!machine) throw new Error(`Machine ${id} not found`);
    return clone(machine);
  }

  async getReadings(id: string, from: string, to: string) {
    const [a, b] = [Date.parse(from), Date.parse(to)];
    return clone((this.readings.get(id) ?? []).filter((r) => Date.parse(r.timestamp) >= a && Date.parse(r.timestamp) <= b));
  }

  async getIncident(machineId: string, incidentId: string) {
    const incident = this.incidents.find((i) => i.machineId === machineId && i.incidentId === incidentId);
    if (!incident) throw new Error(`Incident ${incidentId} not found`);
    const waiting = incident.state === "ASSIGNED" || incident.state === "IN_PROGRESS";
    return { incident: clone(incident), etag: waiting ? `"${incidentId}-${incident.state}"` : null };
  }

  async getMachineIncidents(id: string) {
    return clone(this.incidents.filter((i) => i.machineId === id));
  }

  async getMaintenance(id: string) {
    return clone(this.maintenance.get(id) ?? []);
  }

  async listIncidents(_op: string, state: "OPEN" | "CLOSED") {
    const open = (i: Incident) => i.state !== "RESOLVED";
    return clone(
      this.incidents
        .filter((i) => (state === "OPEN" ? open(i) : !open(i)))
        .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.createdAt.localeCompare(a.createdAt)),
    );
  }

  async listTechnicians() {
    return clone(this.technicians);
  }

  async listParts() {
    return clone(this.parts);
  }

  async getKpis(_op: string, date: string) {
    return seedKpis(date);
  }

  async ackIncident(machineId: string, incidentId: string, action: "ARRIVED" | "RESOLVED", etag?: string) {
    const incident = this.incidents.find((i) => i.incidentId === incidentId && i.machineId === machineId);
    if (!incident) throw new Error(`Incident ${incidentId} not found`);
    if (etag && etag !== (await this.getIncident(machineId, incidentId)).etag) throw new Error("La espera cambió.");
    const machine = this.machines.get(machineId)!;
    const at = new Date(this.now).toISOString();
    incident.timeline.push({ at, event: action, tech: incident.assignedTech });

    if (action === "ARRIVED") {
      incident.state = "IN_PROGRESS";
      machine.status = "MAINTENANCE";
    } else {
      incident.state = "RESOLVED";
      incident.downtimeMinutes = Math.round((this.now - Date.parse(incident.createdAt)) / MINUTE);
      this.faults.delete(machineId);
      machine.status = "OPERATING";
      machine.openIncidentId = undefined;
      const tech = this.technicians.find((t) => t.techId === incident.assignedTech);
      if (tech) {
        tech.queue = Math.max(0, tech.queue - 1);
        tech.availability = tech.queue ? "BUSY" : "AVAILABLE";
      }
      for (const p of incident.partsReserved) {
        const stock = this.parts.find((s) => s.partId === p.partId)?.warehouses.find((w) => w.warehouseId === p.warehouse);
        if (stock) {
          stock.reserved -= p.qty;
          stock.qty -= p.qty;
        }
      }
      this.maintenance.get(machineId)!.unshift({
        maintenanceId: `MNT-${incidentId}`,
        date: at,
        type: "CORRECTIVE",
        engineHours: machine.engineHours,
        partsReplaced: incident.partsReserved.map((p) => p.partId),
        notes: `${incident.aiDiagnosis.component}: ${incident.aiDiagnosis.recommendedAction}`,
      });
    }
    return clone(incident);
  }

  async injectFault(machineId: string, faultType: FaultType) {
    const target = this.machines.get(machineId);
    if (!target) throw new Error(`Machine ${machineId} not found`);
    const affected =
      faultType === "FUEL_BATCH_ZONE"
        ? [...this.machines.values()].filter((m) => m.zoneId === target.zoneId && m.status === "OPERATING")
        : [target];
    for (const m of affected) {
      if (m.status === "IDLE") m.status = "OPERATING";
      this.faults.set(m.machineId, { type: faultType, progress: 0 });
    }
  }
}
