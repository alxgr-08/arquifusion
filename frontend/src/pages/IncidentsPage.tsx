import { useState } from "react";
import { api, IS_MOCK, OPERATION } from "../api";
import { IncidentCard } from "../components/IncidentCard";
import { usePolling } from "../lib/hooks";

export function IncidentsPage() {
  const [state, setState] = useState<"OPEN" | "CLOSED">("OPEN");
  const incidents = usePolling(() => api.listIncidents(OPERATION, state), 2500, [state]);
  const technicians = usePolling(() => api.listTechnicians(OPERATION), 4000);
  const list = incidents.data ?? [];
  const zones = new Set(list.filter((i) => i.zoneIncidentId).map((i) => i.zoneIncidentId));

  return (
    <div className="page grid-side">
      <section>
        <div className="row between">
          <h2>Cola de incidentes {state === "OPEN" ? "abiertos" : "cerrados"} ({list.length})</h2>
          <div className="tabs">
            <button className={state === "OPEN" ? "active" : ""} onClick={() => setState("OPEN")}>Abiertos</button>
            <button className={state === "CLOSED" ? "active" : ""} onClick={() => setState("CLOSED")}>Historial</button>
          </div>
        </div>
        {zones.size > 0 && state === "OPEN" && (
          <p className="zone banner">{zones.size} incidente(s) de zona activo(s): varias máquinas fallan en la misma zona.</p>
        )}
        {incidents.error && <p className="error">{incidents.error}</p>}
        {!incidents.error && list.length === 0 && <p className="muted card">Sin incidentes.{IS_MOCK && " Puedes simular una falla en Flota."}</p>}
        {list.map((i) => (
          <IncidentCard key={i.incidentId} incident={i} onChange={incidents.refresh} />
        ))}
      </section>

      <aside className="card">
        <h2>Técnicos</h2>
        <table>
          <thead>
            <tr><th>Técnico</th><th>Especialidad</th><th>Estado</th><th>Cola</th></tr>
          </thead>
          <tbody>
            {(technicians.data ?? []).map((t) => (
              <tr key={t.techId}>
                <td>{t.name}<small>{t.techId}</small></td>
                <td>{t.specialties.join(", ")}</td>
                <td><span className={`avail ${t.availability.toLowerCase()}`}>{t.availability}</span></td>
                <td>{t.queue}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </aside>
    </div>
  );
}
