import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, OPERATION } from "../api";
import type { FaultType } from "../api/types";
import { HealthBar, SeverityBadge, StatusBadge } from "../components/Badges";
import { FleetMap } from "../components/FleetMap";
import { formatTime, hoursToMaintenance } from "../lib/domain";
import { usePolling, useRole } from "../lib/hooks";

const FAULT_LABEL: Record<FaultType, string> = {
  BEARING_WEAR: "Desgaste de rodamiento",
  OVERHEATING: "Sobrecalentamiento",
  HYDRAULIC_LEAK: "Fuga hidráulica",
  FUEL_BATCH_ZONE: "Combustible contaminado (toda la zona)",
};

export function FleetPage() {
  const navigate = useNavigate();
  const { role } = useRole();
  const machines = usePolling(() => api.listMachines(OPERATION), 3000);
  const technicians = usePolling(() => api.listTechnicians(OPERATION), 5000);
  const list = [...(machines.data ?? [])].sort((a, b) => a.healthScore - b.healthScore);

  const count = (pred: (s: string) => boolean) => list.filter((m) => pred(m.status)).length;
  const alerts = list.filter((m) => m.severity === "WARNING" || m.severity === "CRITICAL").length;

  return (
    <div className="page">
      <section className="stats">
        <Stat label="Máquinas" value={list.length} />
        <Stat label="Operando" value={count((s) => s === "OPERATING")} />
        <Stat label="Inactivas" value={count((s) => s === "IDLE")} />
        <Stat label="Detenidas / mantenimiento" value={count((s) => s === "STOPPED" || s === "MAINTENANCE")} />
        <Stat label="Requieren intervención" value={alerts} tone={alerts ? "danger" : undefined} />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Mapa de la operación {OPERATION}</h2>
          <FleetMap
            machines={list}
            technicians={role === "OPERATOR" ? [] : technicians.data}
            onSelect={(id) => navigate(`/machines/${id}`)}
          />
          <p className="legend">
            <span className="dot ok" /> OK <span className="dot watch" /> Observación <span className="dot warning" /> Alerta{" "}
            <span className="dot critical" /> Crítico <span className="dot tech" /> Técnico
          </p>
        </section>

        <section className="card">
          <h2>Estado de la flota</h2>
          {machines.error && <p className="error">{machines.error}</p>}
          <table>
            <thead>
              <tr>
                <th>Máquina</th>
                <th>Zona</th>
                <th>Estado</th>
                <th>Salud</th>
                <th>Gravedad</th>
                <th>Próx. mant.</th>
                <th>Último dato</th>
              </tr>
            </thead>
            <tbody>
              {list.map((m) => {
                const due = hoursToMaintenance(m.engineHours, m.lastMaintenanceHours, m.maintenanceIntervalHours);
                return (
                  <tr key={m.machineId}>
                    <td>
                      <Link to={`/machines/${m.machineId}`}>{m.machineId}</Link>
                      <small>{m.model}</small>
                    </td>
                    <td>{m.zoneId}</td>
                    <td><StatusBadge status={m.status} /></td>
                    <td><HealthBar value={m.healthScore} severity={m.severity} /></td>
                    <td><SeverityBadge severity={m.severity} /></td>
                    <td className={due < 24 ? "warn" : ""}>{Math.round(due)} h</td>
                    <td>{formatTime(m.lastSeen)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </div>

      {(role === "MAINT_LEAD" || role === "SUPERVISOR") && (
        <FaultInjector machineIds={list.map((m) => m.machineId)} onDone={machines.refresh} />
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "danger" }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function FaultInjector({ machineIds, onDone }: { machineIds: string[]; onDone: () => void }) {
  const [machineId, setMachineId] = useState("EXC-0042");
  const [fault, setFault] = useState<FaultType>("BEARING_WEAR");
  const [sent, setSent] = useState<string>();

  const inject = async () => {
    await api.injectFault(machineId, fault);
    setSent(`Falla "${FAULT_LABEL[fault]}" inyectada en ${machineId}`);
    onDone();
  };

  return (
    <section className="card demo">
      <h2>Demo · inyectar falla</h2>
      <div className="row">
        <select value={machineId} onChange={(e) => setMachineId(e.target.value)}>
          {machineIds.map((id) => (
            <option key={id}>{id}</option>
          ))}
        </select>
        <select value={fault} onChange={(e) => setFault(e.target.value as FaultType)}>
          {(Object.keys(FAULT_LABEL) as FaultType[]).map((f) => (
            <option key={f} value={f}>
              {FAULT_LABEL[f]}
            </option>
          ))}
        </select>
        <button onClick={inject}>Inyectar</button>
        {sent && <span className="muted">{sent}</span>}
      </div>
    </section>
  );
}
