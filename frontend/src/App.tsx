import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { IS_MOCK, OPERATION, setAuthToken, setUnauthorizedHandler } from "./api";
import type { Role } from "./api/types";
import { ToastProvider, useIncidentWatcher } from "./components/Live";
import { LoginScreen } from "./components/LoginScreen";
import { persistSession, restoreSession, signOutGoogle, type Session } from "./lib/auth";
import { ROLE_LABEL } from "./lib/domain";
import { RoleContext, useStoredState } from "./lib/hooks";

const FleetPage = lazy(() => import("./pages/FleetPage").then((m) => ({ default: m.FleetPage })));
const IncidentsPage = lazy(() => import("./pages/IncidentsPage").then((m) => ({ default: m.IncidentsPage })));
const IncidentPage = lazy(() => import("./pages/IncidentPage").then((m) => ({ default: m.IncidentPage })));
const KpisPage = lazy(() => import("./pages/KpisPage").then((m) => ({ default: m.KpisPage })));
const MachinePage = lazy(() => import("./pages/MachinePage").then((m) => ({ default: m.MachinePage })));
const PartsPage = lazy(() => import("./pages/PartsPage").then((m) => ({ default: m.PartsPage })));

/** Which sections each role sees (P9). Visual only: the API enforces access with the grants in DynamoDB. */
const NAV: Record<Role, { to: string; label: string }[]> = {
  OPERATOR: [{ to: "/", label: "Flota" }, { to: "/incidents", label: "Incidentes" }],
  TECHNICIAN: [{ to: "/incidents", label: "Mis incidentes" }, { to: "/", label: "Flota" }],
  MAINT_LEAD: [
    { to: "/", label: "Flota" },
    { to: "/incidents", label: "Incidentes" },
    { to: "/parts", label: "Repuestos" },
    { to: "/kpis", label: "KPIs" },
  ],
  SUPERVISOR: [{ to: "/", label: "Flota" }, { to: "/incidents", label: "Incidentes" }, { to: "/kpis", label: "KPIs" }],
  MANAGER: [{ to: "/kpis", label: "KPIs" }, { to: "/", label: "Flota" }, { to: "/incidents", label: "Incidentes" }, { to: "/parts", label: "Repuestos" }],
};

// A token saved by this tab survives a reload (F5) until it expires.
const restored = IS_MOCK ? undefined : restoreSession();
if (restored) setAuthToken(restored.idToken);

export function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const [role, setRole] = useStoredState<Role>("af.role", "MAINT_LEAD");
  const [session, setSession] = useState<Session | undefined>(restored);
  const signedIn = IS_MOCK || !!session;
  const watch = useIncidentWatcher(signedIn);
  const allowed = new Set(NAV[role].map((n) => n.to));

  const signIn = useCallback((s: Session) => {
    setAuthToken(s.idToken);
    persistSession(s);
    setSession(s);
  }, []);
  const signOut = useCallback(() => {
    setAuthToken(undefined);
    signOutGoogle();
    setSession(undefined);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
    return () => setUnauthorizedHandler(undefined);
  }, [signOut]);

  useEffect(() => {
    if (!session) return;
    const timer = setTimeout(signOut, Math.max(session.expiresAt * 1000 - 60_000 - Date.now(), 0));
    return () => clearTimeout(timer);
  }, [session, signOut]);

  if (!signedIn) return <LoginScreen onSession={signIn} />;

  return (
    <RoleContext.Provider value={{ role, setRole }}>
      <header className="topbar">
        <div className="brand">
          <strong>ArquiFusion</strong> Fleet Intelligence
          <span className="muted"> · {OPERATION}</span>
          {IS_MOCK && <span className="pill">datos simulados</span>}
        </div>
        <nav>
          {NAV[role].map((n) => (
            <NavLink key={n.to} to={n.to} end>
              {n.label}
              {n.to === "/incidents" && watch.open > 0 && (
                <span className={`count ${watch.critical ? "critical" : ""}`} title={`${watch.open} incidente(s) abierto(s)`}>
                  {watch.open}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <label className="role">
          Rol
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
              <option key={r} value={r}>{ROLE_LABEL[r]}</option>
            ))}
          </select>
        </label>
        {session && (
          <span className="user">
            {session.email}
            <button onClick={signOut}>Salir</button>
          </span>
        )}
      </header>
      <main>
        <Suspense fallback={<p className="muted">Cargando…</p>}>
          <Routes>
            <Route path="/" element={<FleetPage />} />
            <Route path="/machines/:machineId" element={<MachinePage />} />
            <Route path="/machines/:machineId/incidents/:incidentId" element={<IncidentPage />} />
            <Route path="/incidents" element={<IncidentsPage />} />
            <Route path="/kpis" element={allowed.has("/kpis") ? <KpisPage /> : <Navigate to="/" />} />
            <Route path="/parts" element={allowed.has("/parts") ? <PartsPage /> : <Navigate to="/" />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </Suspense>
      </main>
    </RoleContext.Provider>
  );
}
