import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { api, OPERATION } from "../api";
import type { MachineKpi } from "../api/types";
import { usePolling } from "../lib/hooks";
import { classify, HIGH_COST, LOW_UTILIZATION } from "../lib/kpi";

const tooltipStyle = { background: "#151b23", border: "1px solid #2a3340" };

export function KpisPage() {
  const date = new Date().toISOString().slice(0, 10);
  const kpis = usePolling(() => api.getKpis(OPERATION, date), 30000);
  const list = kpis.data ?? [];
  const fuel = list.filter((k) => k.fuelLitersPerTonFleet > 0);
  const avg = (f: (k: MachineKpi) => number) => (list.length ? list.reduce((s, k) => s + f(k), 0) / list.length : 0);

  return (
    <div className="page">
      <section className="stats">
        <div className="stat"><strong>{avg((k) => k.utilizationPct).toFixed(0)}%</strong><span>Utilización promedio</span></div>
        <div className="stat"><strong>{list.reduce((s, k) => s + k.idleHours, 0).toFixed(0)} h</strong><span>Horas inactivas (flota)</span></div>
        <div className="stat"><strong>{list.reduce((s, k) => s + k.overuseHours, 0).toFixed(1)} h</strong><span>Horas en sobreuso</span></div>
        <div className="stat danger"><strong>{list.filter((k) => k.fuelAnomaly).length}</strong><span>Anomalías de combustible</span></div>
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Productividad vs. costo ({date})</h2>
          <ResponsiveContainer width="100%" height={320}>
            <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
              <XAxis type="number" dataKey="utilizationPct" name="Utilización" unit="%" domain={[0, 100]} stroke="#8a96a8" />
              <YAxis type="number" dataKey="costPerHour" name="Costo/h" unit=" $" stroke="#8a96a8" />
              <ZAxis range={[90, 90]} />
              <ReferenceLine x={LOW_UTILIZATION} stroke="#d4a017" strokeDasharray="4 4" />
              <ReferenceLine y={HIGH_COST} stroke="#d93025" strokeDasharray="4 4" />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v) => String(v)}
                labelFormatter={() => ""}
              />
              <Scatter isAnimationActive={false} data={list} name="Máquinas">
                {list.map((k) => (
                  <Cell key={k.machineId} fill={classify(k).color} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </section>

        <section className="card">
          <h2>Combustible: litros por tonelada vs. modelo</h2>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={fuel}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
              <XAxis dataKey="machineId" stroke="#8a96a8" angle={-30} textAnchor="end" height={60} />
              <YAxis stroke="#8a96a8" />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend />
              <Bar isAnimationActive={false} dataKey="fuelLitersPerTon" name="Máquina (L/t)" fill="#4fc3f7">
                {fuel.map((k) => (
                  <Cell key={k.machineId} fill={k.fuelAnomaly ? "#d93025" : "#4fc3f7"} />
                ))}
              </Bar>
              <Bar isAnimationActive={false} dataKey="fuelLitersPerTonFleet" name="Promedio del modelo (L/t)" fill="#5b6b80" />
            </BarChart>
          </ResponsiveContainer>
        </section>
      </div>

      <section className="card">
        <h2>Detalle por máquina</h2>
        <table>
          <thead>
            <tr>
              <th>Máquina</th><th>Utilización</th><th>Inactiva</th><th>Sobreuso</th><th>Toneladas</th>
              <th>L/t</th><th>Costo/h</th><th>Clasificación</th>
            </tr>
          </thead>
          <tbody>
            {list.map((k) => {
              const c = classify(k);
              return (
                <tr key={k.machineId}>
                  <td>{k.machineId}<small>{k.model}</small></td>
                  <td>{k.utilizationPct}%</td>
                  <td>{k.idleHours} h</td>
                  <td>{k.overuseHours} h</td>
                  <td>{k.tonsMoved.toLocaleString("es-PE")}</td>
                  <td className={k.fuelAnomaly ? "warn" : ""}>{k.fuelLitersPerTon || "—"}</td>
                  <td>${k.costPerHour}</td>
                  <td><span className="pill" style={{ borderColor: c.color, color: c.color }}>{c.label}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted">Fuente: BigQuery (af_out.daily_kpis) sincronizado a DynamoDB a diario.</p>
      </section>
    </div>
  );
}
