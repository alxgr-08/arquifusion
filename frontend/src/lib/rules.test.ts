import { describe, expect, it } from "vitest";
import type { MachineKpi } from "../api/types";
import { classify } from "./kpi";
import { irregular, stockStatus } from "./parts";
import { hoursToMaintenance, severityFromHealth } from "./domain";

const kpi = (utilizationPct: number, costPerHour: number) => ({ utilizationPct, costPerHour }) as MachineKpi;

describe("domain rules", () => {
  it("maps health to the same thresholds as the backend", () => {
    expect([100, 80, 79, 60, 59, 40, 39].map(severityFromHealth)).toEqual([
      "OK", "OK", "WATCH", "WATCH", "WARNING", "WARNING", "CRITICAL",
    ]);
  });

  it("adjusts maintenance interval by usage factor (P2)", () => {
    expect(hoursToMaintenance(12450, 12000, 500)).toBe(50);
    expect(hoursToMaintenance(12450, 12000, 500, 1.25)).toBe(-50);
  });

  it("classifies productivity vs cost (P7)", () => {
    expect(classify(kpi(18, 410)).label).toBe("Baja productividad + alto costo");
    expect(classify(kpi(96, 200)).label).toBe("Sobreexigida");
    expect(classify(kpi(30, 200)).label).toBe("Subutilizada");
    expect(classify(kpi(70, 200)).label).toBe("Normal");
  });

  it("flags stock shortage, reorder and overstock (P3)", () => {
    const w = (qty: number, reserved: number, forecast: number[]) => ({
      warehouseId: "WH", qty, reserved, reorderPoint: 2, forecastNext4Weeks: forecast,
    });
    expect(stockStatus(w(2, 1, [1, 1, 1, 1]))).toBe("SHORTAGE");
    expect(stockStatus(w(3, 1, [1, 1, 0, 0]))).toBe("REORDER");
    expect(stockStatus(w(20, 0, [1, 1, 1, 1]))).toBe("OVERSTOCK");
    expect(stockStatus(w(6, 0, [1, 1, 1, 1]))).toBe("OK");
  });

  it("detects irregular part demand", () => {
    expect(irregular([2, 9, 1, 2])).toBe(true);
    expect(irregular([5, 6, 5, 7])).toBe(false);
  });
});
