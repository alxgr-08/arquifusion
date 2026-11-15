import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { Incident } from "../api/types";
import { IncidentCard } from "../components/IncidentCard";

export function IncidentPage() {
  const { machineId, incidentId } = useParams();
  const [detail, setDetail] = useState<Incident>();
  const [error, setError] = useState<string>();
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    let current = true;
    setDetail(undefined);
    setError(undefined);
    if (machineId && incidentId) {
      api.getIncident(machineId, incidentId).then(
        (result) => { if (current) setDetail(result.incident); },
        (err) => { if (current) setError(err instanceof Error ? err.message : String(err)); },
      );
    }
    return () => { current = false; };
  }, [machineId, incidentId, revision]);
  return (
    <section className="page">
      <h2>Detalle del incidente</h2>
      {error && <p className="error">{error}</p>}
      {detail?.machineId === machineId && detail?.incidentId === incidentId && detail &&
        <IncidentCard key={detail.incidentId} incident={detail} onChange={refresh} />}
      {!error && !detail && <p>Cargando incidente…</p>}
      <button onClick={refresh}>Actualizar detalle</button>
    </section>
  );
}
