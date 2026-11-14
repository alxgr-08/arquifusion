import { api } from "../api";
import { usePolling } from "../lib/hooks";
import { irregular, stockStatus, type StockStatus } from "../lib/parts";

const STATUS_LABEL: Record<StockStatus, string> = {
  SHORTAGE: "Faltante",
  REORDER: "Reponer",
  OVERSTOCK: "Sobrestock",
  OK: "OK",
};

export function PartsPage() {
  const parts = usePolling(() => api.listParts(), 4000);

  return (
    <div className="page">
      <section className="card">
        <h2>Repuestos por bodega y pronóstico de demanda</h2>
        <table>
          <thead>
            <tr>
              <th>Pieza</th><th>Bodega</th><th>Stock</th><th>Reservado</th><th>Disponible</th>
              <th>Pronóstico 4 semanas</th><th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {(parts.data ?? []).flatMap((p) =>
              p.warehouses.map((w) => {
                const status = stockStatus(w);
                return (
                  <tr key={`${p.partId}-${w.warehouseId}`}>
                    <td>{p.name}<small>{p.partId} · {p.component}</small></td>
                    <td>{w.warehouseId}</td>
                    <td>{w.qty}</td>
                    <td>{w.reserved}</td>
                    <td><strong>{w.qty - w.reserved}</strong></td>
                    <td>
                      <span className="spark">
                        {w.forecastNext4Weeks.map((f, i) => (
                          <span key={i} style={{ height: `${6 + f * 5}px` }} title={`Semana ${i + 1}: ${f}`} />
                        ))}
                      </span>
                      {w.forecastNext4Weeks.join(" · ")}
                      {irregular(w.forecastNext4Weeks) && <span className="pill warn">consumo irregular</span>}
                    </td>
                    <td><span className={`stock ${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span></td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
        <p className="muted">Pronóstico: BigQuery ML ARIMA_PLUS (af_ml.parts_demand). Reserva atómica al crear el incidente.</p>
      </section>
    </div>
  );
}
