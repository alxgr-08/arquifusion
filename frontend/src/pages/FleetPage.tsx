import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, IS_MOCK, OPERATION } from "../api";
import type { FaultType, Machine, MachineStatus } from "../api/types";
import { HealthBar, SeverityBadge, StatusBadge } from "../components/Badges";
import { FleetMap } from "../components/FleetMap";
import { ErrorBanner, LiveStatus, useToast } from "../components/Live";
import { formatTime, hoursToMaintenance, SEVERITY_RANK } from "../lib/domain";
import { usePolling, useRole } from "../lib/hooks";

const FAULT_LABEL: Record<FaultType, string> = {
  BEARING_WEAR: "Desgaste de rodamiento",
  OVERHEATING: "Sobrecalentamiento",
  HYDRAULIC_LEAK: "Fuga hidráulica",
  FUEL_BATCH_ZONE: "Combustible contaminado (toda la zona)",
};

type Filter = "ALL" | "OPERATING" | "IDLE" | "STOPPED" | "ALERT";
type SortKey = "machineId" | "zoneId" | "status" | "healthScore" | "severity" | "due" | "lastSeen";

const FILTERS: Record<Filter, (m: Machine) => boolean> = {
  ALL: () => true,
  OPERATING: (m) => m.status === "OPERATING",
  IDLE: (m) => m.status === "IDLE",
  STOPPED: (m) => m.status === "STOPPED" || m.status === "MAINTENANCE",
  ALERT: (m) => m.severity === "WARNING" || m.severity === "CRITICAL",
};

const due = (m: Machine) => hoursToMaintenance(m.engineHours, m.lastMaintenanceHours, m.maintenanceIntervalHours);
const SORT_VALUE: Record<SortKey, (m: Machine) => string | number> = {
  machineId: (m) => m.machineId,
  zoneId: (m) => m.zoneId,
  status: (m) => m.status as MachineStatus,
  healthScore: (m) => m.healthScore,
  severity: (m) => SEVERITY_RANK[m.severity],
  due,
  lastSeen: (m) => m.lastSeen ?? "",
};

export function FleetPage() {
  const navigate = useNavigate();
  const { role } = useRole();
  const machines = usePolling(() => api.listMachines(OPERATION), 3000);
  const technicians = usePolling(() => api.listTechnicians(OPERATION), 5000);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "healthScore", asc: true });
  const [hover, setHover] = useState<string>();

  const all = machines.data ?? [];
  const q = query.trim().toLowerCase();
  const list = all
    .filter(FILTERS[filter])
    .filter((m) => !q || `${m.machineId} ${m.model} ${m.zoneId}`.toLowerCase().includes(q))
    .sort((a, b) => {
      const [x, y] = [SORT_VALUE[sort.key](a), SORT_VALUE[sort.key](b)];
      const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.asc ? cmp : -cmp;
    });
  const count = (f: Filter) => all.filter(FILTERS[f]).length;

  const header = (key: SortKey, label: string) => (
    <th
      className={`sortable ${sort.key === key ? "sorted" : ""}`}
      onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
      title="Ordenar"
    >
      {label} {sort.key === key ? (sort.asc ? "▲" : "▼") : ""}
    </th>
  );

  return (
    <div className="page">
      <section className="stats">
        <Stat label="Máquinas" value={count("ALL")} active={filter === "ALL"} onClick={() => setFilter("ALL")} />
        <Stat label="Operando" value={count("OPERATING")} active={filter === "OPERATING"} onClick={() => setFilter("OPERATING")} />
        <Stat label="Inactivas" value={count("IDLE")} active={filter === "IDLE"} onClick={() => setFilter("IDLE")} />
        <Stat label="Detenidas / mantenimiento" value={count("STOPPED")} active={filter === "STOPPED"} onClick={() => setFilter("STOPPED")} />
        <Stat
          label="Requieren intervención"
          value={count("ALERT")}
          tone={count("ALERT") ? "danger" : undefined}
          active={filter === "ALERT"}
          onClick={() => setFilter("ALERT")}
        />
      </section>

      <ErrorBanner error={machines.error} onRetry={machines.refresh} />

      <div className="grid-2">
        <section className="card">
          <h2>Mapa de la operación {OPERATION}</h2>
          <FleetMap
            operationId={OPERATION}
            machines={list}
            technicians={role === "OPERATOR" ? [] : technicians.data}
            onSelect={(id) => navigate(`/machines/${id}`)}
            onHover={setHover}
            highlight={hover}
          />
          <p className="legend">
            <span className="dot ok" /> OK <span className="dot watch" /> Observación <span className="dot warning" /> Alerta{" "}
            <span className="dot critical" /> Crítico <span className="dot tech" /> Técnico
          </p>
        </section>

        <section className="card">
          <div className="row between">
            <h2>Estado de la flota</h2>
            <LiveStatus updatedAt={machines.updatedAt} error={machines.error} loading={machines.loading} onRefresh={machines.refresh} />
          </div>
          <div className="row toolbar">
            <input
              type="search"
              placeholder="Buscar máquina, modelo o zona…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {filter !== "ALL" && (
              <button className="chip" onClick={() => setFilter("ALL")}>Filtro: {filterLabel(filter)} ×</button>
            )}
          </div>
          <table>
            <thead>
              <tr>
                {header("machineId", "Máquina")}
                {header("zoneId", "Zona")}
                {header("status", "Estado")}
                {header("healthScore", "Salud")}
                {header("severity", "Gravedad")}
                {header("due", "Próx. mant.")}
                {header("lastSeen", "Último dato")}
              </tr>
            </thead>
            <tbody>
              {list.map((m) => {
                const hours = due(m);
                return (
                  <tr
                    key={m.machineId}
                    className={`clickable ${hover === m.machineId ? "hl" : ""}`}
                    onClick={() => navigate(`/machines/${m.machineId}`)}
                    onMouseEnter={() => setHover(m.machineId)}
                    onMouseLeave={() => setHover(undefined)}
                  >
                    <td>
                      <Link to={`/machines/${m.machineId}`} onClick={(e) => e.stopPropagation()}>{m.machineId}</Link>
                      <small>{m.model}</small>
                    </td>
                    <td>{m.zoneId}</td>
                    <td><StatusBadge status={m.status} /></td>
                    <td><HealthBar value={m.healthScore} severity={m.severity} /></td>
                    <td><SeverityBadge severity={m.severity} /></td>
                    <td className={hours < 24 ? "warn" : ""}>{Math.round(hours)} h</td>
                    <td>{formatTime(m.lastSeen)}</td>
                  </tr>
                );
              })}
              {machines.data && list.length === 0 && (
                <tr><td colSpan={7} className="muted">Ninguna máquina coincide con el filtro.</td></tr>
              )}
              {!machines.data && !machines.error && (
                <tr><td colSpan={7} className="muted">Cargando flota…</td></tr>
              )}
            </tbody>
          </table>
        </section>
      </div>

      {IS_MOCK && (role === "MAINT_LEAD" || role === "SUPERVISOR") && (
        <FaultInjector machineIds={all.map((m) => m.machineId)} onDone={machines.refresh} />
      )}
    </div>
  );
}

function filterLabel(f: Filter): string {
  return { ALL: "todas", OPERATING: "operando", IDLE: "inactivas", STOPPED: "detenidas", ALERT: "requieren intervención" }[f];
}

function Stat({ label, value, tone, active, onClick }: {
  label: string;
  value: number;
  tone?: "danger";
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button className={`stat ${tone ?? ""} ${active ? "active" : ""}`} onClick={onClick} title={`Filtrar: ${label}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </button>
  );
}

function FaultInjector({ machineIds, onDone }: { machineIds: string[]; onDone: () => void }) {
  const toast = useToast();
  const [machineId, setMachineId] = useState("EXC-0042");
  const [fault, setFault] = useState<FaultType>("BEARING_WEAR");
  const [busy, setBusy] = useState(false);

  const inject = async () => {
    setBusy(true);
    try {
      await api.injectFault(machineId, fault);
      toast({ text: `Falla "${FAULT_LABEL[fault]}" inyectada en ${machineId}`, severity: "WATCH" });
      onDone();
    } catch (e) {
      toast({ text: `No se pudo inyectar la falla: ${e instanceof Error ? e.message : e}`, severity: "CRITICAL" });
    } finally {
      setBusy(false);
    }
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
        <button disabled={busy} onClick={inject}>{busy ? "Inyectando…" : "Inyectar"}</button>
      </div>
    </section>
  );
}
