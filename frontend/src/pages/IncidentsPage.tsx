import { useState } from "react";
import { api, IS_MOCK, OPERATION } from "../api";
import type { Severity } from "../api/types";
import { IncidentCard } from "../components/IncidentCard";
import { ErrorBanner, LiveStatus } from "../components/Live";
import { SEVERITY_COLOR } from "../lib/domain";
import { usePolling, useRole } from "../lib/hooks";

const SEVERITIES: Severity[] = ["CRITICAL", "WARNING", "WATCH"];

export function IncidentsPage() {
  const { role } = useRole();
  const [state, setState] = useState<"OPEN" | "CLOSED">("OPEN");
  const [severity, setSeverity] = useState<Severity>();
  const [query, setQuery] = useState("");
  const [selectedTech, setSelectedTech] = useState<string>();
  const incidents = usePolling(() => api.listIncidents(OPERATION, state), 2500, [state]);
  const technicians = usePolling(() => api.listTechnicians(OPERATION), 4000);
  const all = incidents.data ?? [];
  const q = query.trim().toLowerCase();
  const list = all
    .filter((i) => !severity || i.severity === severity)
    .filter((i) => !selectedTech || i.assignedTech === selectedTech)
    .filter((i) => !q || `${i.incidentId} ${i.machineId} ${i.zoneId} ${i.aiDiagnosis?.component ?? ""}`.toLowerCase().includes(q));
  const zones = new Set(all.filter((i) => i.zoneIncidentId).map((i) => i.zoneIncidentId));

  return (
    <div className="page grid-side">
      <section>
        <div className="row between">
          <h2>
            Cola de incidentes {state === "OPEN" ? "abiertos" : "cerrados"} ({list.length}
            {list.length !== all.length ? ` de ${all.length}` : ""})
          </h2>
          <div className="row">
            <LiveStatus updatedAt={incidents.updatedAt} error={incidents.error} loading={incidents.loading} onRefresh={incidents.refresh} />
            <div className="tabs">
              <button className={state === "OPEN" ? "active" : ""} onClick={() => setState("OPEN")}>Abiertos</button>
              <button className={state === "CLOSED" ? "active" : ""} onClick={() => setState("CLOSED")}>Historial</button>
            </div>
          </div>
        </div>

        <div className="row toolbar">
          <input
            type="search"
            placeholder="Buscar incidente, máquina, zona o componente…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {SEVERITIES.map((s) => {
            const n = all.filter((i) => i.severity === s).length;
            return (
              <button
                key={s}
                className={`chip ${severity === s ? "active" : ""}`}
                style={{ borderColor: severity === s ? SEVERITY_COLOR[s] : undefined }}
                onClick={() => setSeverity(severity === s ? undefined : s)}
              >
                <span className="dot" style={{ background: SEVERITY_COLOR[s] }} /> {s} {n}
              </button>
            );
          })}
          {selectedTech && (
            <button className="chip active" onClick={() => setSelectedTech(undefined)}>Técnico {selectedTech} ×</button>
          )}
        </div>

        {zones.size > 0 && state === "OPEN" && (
          <p className="zone banner">{zones.size} incidente(s) de zona activo(s): varias máquinas fallan en la misma zona.</p>
        )}
        <ErrorBanner error={incidents.error} onRetry={incidents.refresh} />
        {!incidents.data && !incidents.error && <p className="muted card">Cargando incidentes…</p>}
        {incidents.data && list.length === 0 && (
          <p className="muted card">
            {all.length ? "Ningún incidente coincide con el filtro." : "Sin incidentes."}
            {IS_MOCK && !all.length && state === "OPEN" && role !== "TECHNICIAN" && " Puedes simular una falla en Flota."}
          </p>
        )}
        {list.map((i) => (
          <IncidentCard key={i.incidentId} incident={i} onChange={incidents.refresh} />
        ))}
      </section>

      <aside className="card">
        <h2>Técnicos</h2>
        <p className="muted hint">Haz clic en un técnico para ver solo sus incidentes.</p>
        <table>
          <thead>
            <tr><th>Técnico</th><th>Especialidad</th><th>Estado</th><th>Cola</th></tr>
          </thead>
          <tbody>
            {(technicians.data ?? []).map((t) => (
              <tr
                key={t.techId}
                className={`clickable ${selectedTech === t.techId ? "hl" : ""}`}
                onClick={() => setSelectedTech(selectedTech === t.techId ? undefined : t.techId)}
              >
                <td>{t.name}<small>{t.techId}</small></td>
                <td>{t.specialties?.join(", ") ?? "—"}</td>
                <td><span className={`avail ${(t.availability ?? "").toLowerCase()}`}>{t.availability ?? "—"}</span></td>
                <td>{t.queue}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </aside>
    </div>
  );
}
