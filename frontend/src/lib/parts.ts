import type { WarehouseStock } from "../api/types";

export type StockStatus = "SHORTAGE" | "REORDER" | "OVERSTOCK" | "OK";

/** Shortage: available stock does not cover the next 2 weeks of forecast demand. */
export function stockStatus(w: WarehouseStock): StockStatus {
  const available = w.qty - w.reserved;
  const next2 = w.forecastNext4Weeks.slice(0, 2).reduce((a, b) => a + b, 0);
  const month = w.forecastNext4Weeks.reduce((a, b) => a + b, 0);
  if (available < next2) return "SHORTAGE";
  if (available <= w.reorderPoint) return "REORDER";
  if (month > 0 ? w.qty > 3 * month : w.qty > 2) return "OVERSTOCK";
  return "OK";
}

/** Irregular demand: one forecast week far above the average of the others. */
export function irregular(forecast: number[]): boolean {
  const max = Math.max(...forecast);
  const rest = forecast.filter((_, i) => i !== forecast.indexOf(max));
  const avg = rest.reduce((a, b) => a + b, 0) / Math.max(rest.length, 1);
  return max >= 3 && max > 2.5 * Math.max(avg, 1);
}
