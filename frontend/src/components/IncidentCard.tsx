import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ApiRequestError, errorMessage } from "../api/client";
import type { Incident } from "../api/types";
import { formatTime, METRIC_LABEL, ROLE_LABEL } from "../lib/domain";
import { useRole } from "../lib/hooks";
import { SeverityBadge } from "./Badges";
import { useToast } from "./Live";

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

export function IncidentCard({ incident, compact, onChange }: { incident: Incident; compact?: boolean; onChange: () => void }) {
  const { role } = useRole();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [ackError, setAckError] = useState<string>();
  const [ackInfo, setAckInfo] = useState<string>();
  const [review, setReview] = useState<{ incident: Incident; etag: string | null }>();
  const recheck = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(recheck.current), []);
  const i = review?.incident ?? incident;
  const inspect = async (keepMessages = false) => {
    setBusy(true);
    if (!keepMessages) {
      setAckError(undefined);
      setAckInfo(undefined);
    }
    try {
      const seen = await api.getIncident(incident.machineId, incident.incidentId);
      setReview(seen);
      setAckInfo(
        !seen.etag && seen.incident.state !== "RESOLVED"
          ? "El flujo aún no tiene una espera activa para confirmar. Vuelve a revisar en unos segundos."
          : undefined,
      );
    } catch (err) {
      setReview(undefined);
      setAckError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const ack = async (action: "ARRIVED" | "RESOLVED") => {
    if (!review?.etag) return;
    setBusy(true);
    setAckError(undefined);
    setAckInfo(undefined);
    try {
      const updated = await api.ackIncident(i.machineId, i.incidentId, action, review.etag);
      toast({
        text: action === "ARRIVED" ? `Llegada confirmada en ${i.machineId}` : `${i.incidentId} resuelto`,
        severity: "OK",
      });
      setReview({ incident: updated, etag: null });
      onChange();
      if (action === "ARRIVED") {
        // The workflow arms the resolution wait a moment after the arrival; fetch its ETag for the next click.
        setAckInfo("Llegada registrada. Preparando la confirmación de resolución…");
        recheck.current = setTimeout(() => inspect(true), 4000);
      }
    } catch (err) {
      setAckError(errorMessage(err));
      if (err instanceof ApiRequestError && [409, 412, 428].includes(err.status)) {
        onChange();
        await inspect(true); // show the current wait so the user can decide again
      } else if (err instanceof ApiRequestError && [401, 403].includes(err.status)) {
        setReview(undefined);
      }
    } finally {
      setBusy(false);
    }
  };
  const canAct = role === "TECHNICIAN" || role === "MAINT_LEAD";

  return (
    <article className={`incident sev-${i.severity.toLowerCase()}`}>
      <header className="row between">
        <div className="row">
          <SeverityBadge severity={i.severity} />
          <Link to={`/machines/${i.machineId}/incidents/${i.incidentId}`}><strong>{i.incidentId}</strong></Link>
          <Link to={`/machines/${i.machineId}`}>{i.machineId}</Link>
          <span className="muted">zona {i.zoneId} · {formatTime(i.createdAt)}</span>
        </div>
        <span className="pill">{STATE_LABEL[i.state]}</span>
      </header>

      {i.zoneIncidentId && i.aiDiagnosis?.zoneCause && (
        <p className="zone">⚠ Incidente de zona {i.zoneIncidentId}: {i.aiDiagnosis.zoneCause}</p>
      )}

      {i.messages?.[role] && (
        <p className="message">
          <span className="muted">Para {ROLE_LABEL[role]}:</span> {i.messages[role]}
        </p>
      )}

      {!compact && (
        <>
          {i.aiDiagnosis && <div className="diag">
            <span className="tag">IA · {i.aiDiagnosis.source === "BEDROCK" ? "Claude (Bedrock)" : "Reglas"}</span>
            <strong>{i.aiDiagnosis.probableCause}</strong>
            <span>Acción: {i.aiDiagnosis.recommendedAction}</span>
            <span className="muted">
              Componente {i.aiDiagnosis.component} · confianza {i.aiDiagnosis.confidence} · ~
              {i.aiDiagnosis.estimatedRepairMinutes} min
            </span>
          </div>}
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
          {(!review?.etag) && <button disabled={busy} onClick={() => inspect()}>Revisar espera y asignación</button>}
          {review?.etag && i.state === "ASSIGNED" && <button disabled={busy} onClick={() => ack("ARRIVED")}>Confirmar llegada</button>}
          {review?.etag && i.state === "IN_PROGRESS" && <button disabled={busy} onClick={() => ack("RESOLVED")}>Confirmar resolución</button>}
        </div>
      )}
      {ackInfo && !ackError && <p className="muted">{ackInfo}</p>}
      {ackError && <p className="error">{ackError}</p>}
    </article>
  );
}
