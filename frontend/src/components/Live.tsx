import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, OPERATION } from "../api";
import type { Incident, Severity } from "../api/types";
import { SEVERITY_COLOR } from "../lib/domain";
import { usePolling, useNow } from "../lib/hooks";

// ------------------------------------------------------------------------------------------- live status
/** "● En vivo · hace 4 s" with a manual refresh button; turns red when the last request failed. */
export function LiveStatus({ updatedAt, error, loading, onRefresh }: {
  updatedAt?: number;
  error?: string;
  loading?: boolean;
  onRefresh: () => void;
}) {
  const now = useNow();
  const ago = updatedAt ? Math.max(0, Math.round((now - updatedAt) / 1000)) : undefined;
  const label = error ? "Sin conexión" : ago === undefined ? "Conectando…" : ago < 2 ? "Actualizado ahora" : `Actualizado hace ${ago} s`;
  return (
    <span className={`live ${error ? "down" : ""}`} title={error}>
      <span className="live-dot" />
      {label}
      <button className={`icon ${loading ? "spin" : ""}`} onClick={onRefresh} title="Actualizar ahora" aria-label="Actualizar ahora">
        ⟳
      </button>
    </span>
  );
}

/** Error banner with a retry button instead of a bare red line. */
export function ErrorBanner({ error, onRetry }: { error?: string; onRetry: () => void }) {
  if (!error) return null;
  return (
    <div className="banner-error" role="alert">
      <span>No se pudo cargar la información: {error}</span>
      <button onClick={onRetry}>Reintentar</button>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- toasts
interface Toast {
  id: number;
  text: string;
  severity?: Severity;
  to?: string;
}

const ToastContext = createContext<(t: Omit<Toast, "id">) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = next.current++;
      setToasts((ts) => [...ts.slice(-3), { ...t, id }]);
      setTimeout(() => dismiss(id), 7000);
    },
    [dismiss],
  );
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className="toast" style={{ borderLeftColor: t.severity ? SEVERITY_COLOR[t.severity] : undefined }}>
            {t.to ? <Link to={t.to} onClick={() => dismiss(t.id)}>{t.text}</Link> : <span>{t.text}</span>}
            <button className="icon" onClick={() => dismiss(t.id)} aria-label="Cerrar">×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ------------------------------------------------------------------------------------------- incident watcher
const STATE_TEXT: Record<Incident["state"], string> = {
  OPEN: "abierto",
  ASSIGNED: "técnico asignado",
  IN_PROGRESS: "técnico en sitio",
  RESOLVED: "resuelto",
};

/** Watches the open-incident queue from any page: toasts on new incidents and state changes; returns the count. */
export function useIncidentWatcher(enabled: boolean): { open: number; critical: number } {
  const toast = useToast();
  const incidents = usePolling(() => (enabled ? api.listIncidents(OPERATION, "OPEN") : Promise.resolve([])), 4000, [enabled]);
  const seen = useRef<Map<string, Incident["state"]>>(undefined);

  useEffect(() => {
    const list = incidents.data;
    if (!list) return;
    const current = new Map(list.map((i) => [i.incidentId, i.state] as const));
    const before = seen.current;
    seen.current = current;
    if (!before) return; // first load: nothing is "new"
    for (const i of list) {
      const to = `/machines/${i.machineId}/incidents/${i.incidentId}`;
      const prev = before.get(i.incidentId);
      if (!prev) toast({ text: `Nuevo incidente ${i.severity} en ${i.machineId} (${i.incidentId})`, severity: i.severity, to });
      else if (prev !== i.state) toast({ text: `${i.incidentId} · ${i.machineId}: ${STATE_TEXT[i.state]}`, severity: i.severity, to });
    }
    for (const id of before.keys()) {
      if (!current.has(id)) toast({ text: `Incidente ${id} cerrado`, severity: "OK" });
    }
  }, [incidents.data, toast]);

  const list = incidents.data ?? [];
  return { open: list.length, critical: list.filter((i) => i.severity === "CRITICAL").length };
}
