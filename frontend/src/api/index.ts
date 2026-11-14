import { HttpFleetApi, type FleetApi } from "./client";
import { MockFleetApi } from "../mock/mockApi";
import type { FaultType } from "./types";

export const OPERATION = import.meta.env.VITE_OPERATION_ID || "MINA-NORTE";
export const IS_MOCK = (import.meta.env.VITE_API_MODE || "mock") !== "live";
export const MOCK_TICK_MS = 2000;

let token: string | undefined;
export const setAuthToken = (value: string | undefined) => {
  token = value;
};

function createApi(): FleetApi {
  if (!IS_MOCK) return new HttpFleetApi(import.meta.env.VITE_API_URL, () => token);
  const mock = new MockFleetApi();
  setInterval(() => mock.tick(), MOCK_TICK_MS);
  // Demo rehearsal: ?fault=EXC-0042:BEARING_WEAR injects a fault on load (several separated by commas).
  for (const spec of new URLSearchParams(globalThis.location?.search ?? "").get("fault")?.split(",") ?? []) {
    const [machineId, faultType] = spec.split(":");
    mock.injectFault(machineId, faultType as FaultType).catch(console.error);
  }
  return mock;
}

export const api: FleetApi = createApi();
