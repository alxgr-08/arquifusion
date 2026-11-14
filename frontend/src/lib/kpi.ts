import type { MachineKpi } from "../api/types";

export const LOW_UTILIZATION = 40;
export const HIGH_COST = 300;
export const OVERUSE = 90;

export function classify(k: MachineKpi): { label: string; color: string } {
  if (k.utilizationPct < LOW_UTILIZATION && k.costPerHour > HIGH_COST)
    return { label: "Baja productividad + alto costo", color: "#d93025" };
  if (k.utilizationPct >= OVERUSE) return { label: "Sobreexigida", color: "#e8710a" };
  if (k.utilizationPct < LOW_UTILIZATION) return { label: "Subutilizada", color: "#d4a017" };
  return { label: "Normal", color: "#2e9e5b" };
}
