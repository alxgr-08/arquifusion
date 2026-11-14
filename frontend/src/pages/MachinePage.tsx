import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { HealthBar, SeverityBadge, StatusBadge } from "../components/Badges";
import { IncidentCard } from "../components/IncidentCard";
import { formatDateTime, formatTime, hoursToMaintenance, METRIC_LABEL } from "../lib/domain";
import { usePolling } from "../lib/hooks";

const CHART_METRICS = ["temperatureC", "vibrationMmS", "pressureBar", "fuelRateLph"] as const;

export function MachinePage() {
  const { machineId = "" } = useParams();
  const [metric, setMetric] = useState<(typeof CHART_METRICS)[number]>("temperatureC");

  const machine = usePolling(() => api.getMachine(machineId), 3000, [machineId]);
  const readings = usePolling(
    () => {
      const to = new Date(Date.now() + 86_400_000).toISOString();
      const from = new Date(Date.now() - 86_400_000).toISOString();
      return api.getReadings(machineId, from, to);
    },
    3000,
    [machineId],
  );
  const incidents = usePolling(() => api.getMachineIncidents(machineId), 4000, [machineId]);
  const maintenance = usePolling(() => api.getMaintenance(machineId), 10000, [machineId]);

  const m = machine.data;
  if (machine.error) return <p className="error">{machine.error}</p>;
  if (!m) return <p className="muted">Cargando…</p>;

  const due = hoursToMaintenance(m.engineHours, m.lastMaintenanceHours, m.maintenanceIntervalHours);
  const data = (readings.data ?? []).slice(-90).map((r) => ({
    time: formatTime(r.timestamp),
    health: r.healthScore,
    value: r.metrics[metric],
  }));

  return (
    <div className="page">
      <p><Link to="/">← Flota</Link></p>
      <section className="card machine-head">
        <div>
          <h2>{m.machineId}</h2>
          <span className="muted">
            {m.model} · {m.machineType} · zona {m.zoneId}
          </span>
        </div>
        <StatusBadge status={m.status} />
        <SeverityBadge severity={m.severity} />
        <div className="grow"><HealthBar value={m.healthScore} severity={m.severity} /></div>
        <div className="kv">
          <span>Horas motor</span>
          <strong>{m.engineHours.toFixed(1)}</strong>
        </div>
        <div className="kv">
          <span>Próximo mantenimiento</span>
          <strong className={due < 24 ? "warn" : ""}>{Math.round(due)} h</strong>
        </div>
      </section>

      <section className="card">
        <div className="row between">
          <h2>Salud y sensores (tiempo real)</h2>
          <select value={metric} onChange={(e) => setMetric(e.target.value as typeof metric)}>
            {CHART_METRICS.map((k) => (
              <option key={k} value={k}>{METRIC_LABEL[k]}</option>
            ))}
          </select>
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
            <XAxis dataKey="time" minTickGap={30} stroke="#8a96a8" />
            <YAxis yAxisId="h" domain={[0, 100]} stroke="#8a96a8" />
            <YAxis yAxisId="v" orientation="right" domain={["auto", "auto"]} stroke="#8a96a8" />
            <Tooltip contentStyle={{ background: "#151b23", border: "1px solid #2a3340" }} />
            <Legend />
            <Line isAnimationActive={false} yAxisId="h" dataKey="health" name="Salud" stroke="#4fc3f7" dot={false} strokeWidth={2} />
            <Line isAnimationActive={false} yAxisId="v" dataKey="value" name={METRIC_LABEL[metric]} stroke="#f6a623" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Historial de incidentes</h2>
          {(incidents.data ?? []).length === 0 && <p className="muted">Sin incidentes.</p>}
          {(incidents.data ?? []).map((i) => (
            <IncidentCard key={i.incidentId} incident={i} compact onChange={incidents.refresh} />
          ))}
        </section>
        <section className="card">
          <h2>Mantenimientos y piezas</h2>
          <table>
            <thead>
              <tr><th>Fecha</th><th>Tipo</th><th>Horas</th><th>Piezas</th><th>Nota</th></tr>
            </thead>
            <tbody>
              {(maintenance.data ?? []).map((mt) => (
                <tr key={mt.maintenanceId}>
                  <td>{formatDateTime(mt.date)}</td>
                  <td>{mt.type === "PREVENTIVE" ? "Preventivo" : "Correctivo"}</td>
                  <td>{Math.round(mt.engineHours)}</td>
                  <td>{mt.partsReplaced.join(", ") || "—"}</td>
                  <td>{mt.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
