import { lazy, Suspense, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { IS_MOCK, OPERATION } from "./api";
import type { Role } from "./api/types";
import { ROLE_LABEL } from "./lib/domain";
import { RoleContext } from "./lib/hooks";

const FleetPage = lazy(() => import("./pages/FleetPage").then((m) => ({ default: m.FleetPage })));
const IncidentsPage = lazy(() => import("./pages/IncidentsPage").then((m) => ({ default: m.IncidentsPage })));
const KpisPage = lazy(() => import("./pages/KpisPage").then((m) => ({ default: m.KpisPage })));
const MachinePage = lazy(() => import("./pages/MachinePage").then((m) => ({ default: m.MachinePage })));
const PartsPage = lazy(() => import("./pages/PartsPage").then((m) => ({ default: m.PartsPage })));

/** Which sections each Cognito group sees (P9: each role gets the information it needs). */
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

export function App() {
  const [role, setRole] = useState<Role>("MAINT_LEAD");
  const allowed = new Set(NAV[role].map((n) => n.to));

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
      </header>
      <main>
        <Suspense fallback={<p className="muted">Cargando…</p>}>
          <Routes>
            <Route path="/" element={<FleetPage />} />
            <Route path="/machines/:machineId" element={<MachinePage />} />
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
