import type { MachineStatus, Severity } from "../api/types";
import { SEVERITY_COLOR, STATUS_LABEL } from "../lib/domain";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="badge" style={{ background: SEVERITY_COLOR[severity] }}>
      {severity}
    </span>
  );
}

export function StatusBadge({ status }: { status: MachineStatus }) {
  return <span className={`status status-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

export function HealthBar({ value, severity }: { value: number; severity: Severity }) {
  return (
    <div className="health" title={`Salud ${value}/100`}>
      <div className="health-fill" style={{ width: `${value}%`, background: SEVERITY_COLOR[severity] }} />
      <span>{Math.round(value)}</span>
    </div>
  );
}
