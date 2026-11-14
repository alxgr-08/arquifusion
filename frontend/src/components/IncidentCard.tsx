import { Link } from "react-router-dom";
import { api } from "../api";
import type { Incident } from "../api/types";
import { formatTime, METRIC_LABEL, ROLE_LABEL } from "../lib/domain";
import { useRole } from "../lib/hooks";
import { SeverityBadge } from "./Badges";

const STATE_LABEL: Record<Incident["state"], string> = {
  OPEN: "Abierto",
  ASSIGNED: "Asignado",
  IN_PROGRESS: "En reparación",
  RESOLVED: "Resuelto",
};

const EVENT_LABEL: Record<string, string> = {
  DETECTED: "Detectado",
  ASSIGNED: "Asignado",
  ARRIVED: "Técnico llegó",
  RESOLVED: "Resuelto",
  ESCALATED: "Escalado",
};

export function IncidentCard({ incident: i, compact, onChange }: { incident: Incident; compact?: boolean; onChange: () => void }) {
  const { role } = useRole();
  const ack = async (action: "ARRIVED" | "RESOLVED") => {
    await api.ackIncident(i.machineId, i.incidentId, action);
    onChange();
  };
  const canAct = role === "TECHNICIAN" || role === "MAINT_LEAD";

  return (
    <article className={`incident sev-${i.severity.toLowerCase()}`}>
      <header className="row between">
        <div className="row">
          <SeverityBadge severity={i.severity} />
          <strong>{i.incidentId}</strong>
          <Link to={`/machines/${i.machineId}`}>{i.machineId}</Link>
          <span className="muted">zona {i.zoneId} · {formatTime(i.createdAt)}</span>
        </div>
        <span className="pill">{STATE_LABEL[i.state]}</span>
      </header>

      {i.zoneIncidentId && i.aiDiagnosis.zoneCause && (
        <p className="zone">⚠ Incidente de zona {i.zoneIncidentId}: {i.aiDiagnosis.zoneCause}</p>
      )}

      <p className="message">
        <span className="muted">Para {ROLE_LABEL[role]}:</span> {i.messages[role]}
      </p>

      {!compact && (
        <>
          <div className="diag">
            <span className="tag">IA · {i.aiDiagnosis.source === "BEDROCK" ? "Claude (Bedrock)" : "Reglas"}</span>
            <strong>{i.aiDiagnosis.probableCause}</strong>
            <span>Acción: {i.aiDiagnosis.recommendedAction}</span>
            <span className="muted">
              Componente {i.aiDiagnosis.component} · confianza {i.aiDiagnosis.confidence} · ~
              {i.aiDiagnosis.estimatedRepairMinutes} min
            </span>
          </div>
          {i.signals.length > 0 && (
            <ul className="signals">
              {i.signals.map((s) => (
                <li key={s.metric}>
                  {METRIC_LABEL[s.metric] ?? s.metric}: <strong>{s.value.toFixed(1)}</strong> (normal {s.baseline}) · z=
                  {s.zScore}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <div className="row wrap">
        <span>Técnico: <strong>{i.assignedTech ?? "sin asignar"}</strong>{i.etaMinutes ? ` · ETA ${i.etaMinutes} min` : ""}</span>
        {i.partsReserved.length > 0 && (
          <span>Repuestos: {i.partsReserved.map((p) => `${p.partId} (${p.warehouse})`).join(", ")}</span>
        )}
        {i.recurrenceOf && <span className="warn">Reincidencia de {i.recurrenceOf}</span>}
        {i.downtimeMinutes !== undefined && <span>Detenida {i.downtimeMinutes} min</span>}
      </div>

      <ol className="timeline">
        {i.timeline.map((t, idx) => (
          <li key={idx}>
            {formatTime(t.at)} · {EVENT_LABEL[t.event]}
            {t.tech ? ` (${t.tech})` : ""}
          </li>
        ))}
      </ol>

      {canAct && i.state !== "RESOLVED" && (
        <div className="row">
          {i.state === "ASSIGNED" && <button onClick={() => ack("ARRIVED")}>Confirmar llegada</button>}
          {i.state === "IN_PROGRESS" && <button onClick={() => ack("RESOLVED")}>Cerrar incidente</button>}
        </div>
      )}
    </article>
  );
}
