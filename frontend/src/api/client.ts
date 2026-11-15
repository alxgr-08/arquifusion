import type {
  FaultType,
  Incident,
  Machine,
  MachineKpi,
  MachineStatus,
  Maintenance,
  PartStock,
  Reading,
  Technician,
} from "./types";

export interface FleetApi {
  getIncident(machineId: string, incidentId: string): Promise<{ incident: Incident; etag: string | null }>;
  listMachines(operationId: string, status?: MachineStatus): Promise<Machine[]>;
  getMachine(machineId: string): Promise<Machine>;
  getReadings(machineId: string, from: string, to: string): Promise<Reading[]>;
  getMachineIncidents(machineId: string): Promise<Incident[]>;
  getMaintenance(machineId: string): Promise<Maintenance[]>;
  listIncidents(operationId: string, state: "OPEN" | "CLOSED"): Promise<Incident[]>;
  listTechnicians(operationId: string): Promise<Technician[]>;
  listParts(operationId?: string): Promise<PartStock[]>;
  getKpis(operationId: string, date: string): Promise<MachineKpi[]>;
  ackIncident(machineId: string, incidentId: string, action: "ARRIVED" | "RESOLVED", etag: string): Promise<Incident>;
  injectFault(machineId: string, faultType: FaultType): Promise<void>;
}

const ACK_ATTEMPTS = 4;

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
  }
}

/** API Gateway client (contracts/api.md). Token = Google ID token (JWT authorizer). */
export class HttpFleetApi implements FleetApi {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | undefined,
    private readonly onUnauthorized?: () => void,
  ) {}

  private async response(path: string, init?: RequestInit): Promise<Response> {
    const token = this.getToken();
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.();
      const body = await response.json().catch(() => undefined);
      const err = body?.error ?? { code: "HTTP_ERROR", message: response.statusText };
      const delay = response.headers.get("Retry-After");
      throw new ApiRequestError(response.status, err.code, err.message, delay && /^\d+$/.test(delay) ? Number(delay) : null);
    }
    return response;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.response(path, init);
    return response.status === 204 ? undefined as T : response.json();
  }

  async getIncident(machineId: string, incidentId: string) {
    const response = await this.response(`/machines/${encodeURIComponent(machineId)}/incidents/${encodeURIComponent(incidentId)}`);
    const etag = response.headers.get("ETag");
    return { incident: await response.json() as Incident, etag: etag && /^"[A-Za-z0-9_-]{1,128}"$/.test(etag) ? etag : null };
  }

  listMachines(op: string, status?: MachineStatus) {
    const q = status ? `?status=${status}` : "";
    return this.request<Machine[]>(`/operations/${op}/machines${q}`);
  }
  getMachine(id: string) {
    return this.request<Machine>(`/machines/${id}`);
  }
  getReadings(id: string, from: string, to: string) {
    return this.request<Reading[]>(`/machines/${id}/readings?from=${from}&to=${to}`);
  }
  getMachineIncidents(id: string) {
    return this.request<Incident[]>(`/machines/${id}/incidents`);
  }
  getMaintenance(id: string) {
    return this.request<Maintenance[]>(`/machines/${id}/maintenance`);
  }
  listIncidents(op: string, state: "OPEN" | "CLOSED") {
    return this.request<Incident[]>(`/operations/${op}/incidents?state=${state}`);
  }
  listTechnicians(op: string) {
    return this.request<Technician[]>(`/operations/${op}/technicians`);
  }
  listParts(operationId?: string) {
    return this.request<PartStock[]>(operationId ? `/parts?operationId=${encodeURIComponent(operationId)}` : "/parts");
  }
  getKpis(op: string, date: string) {
    return this.request<MachineKpi[]>(`/operations/${op}/kpis?date=${date}`);
  }
  /** Bind every retry to the wait the user reviewed, including after a lost HTTP response. */
  async ackIncident(machineId: string, incidentId: string, action: "ARRIVED" | "RESOLVED", etag: string) {
    if (!/^"[A-Za-z0-9_-]{1,128}"$/.test(etag)) throw new Error("Revisa la espera antes de confirmar.");
    const actorToken = this.getToken();
    const path = `/machines/${encodeURIComponent(machineId)}/incidents/${encodeURIComponent(incidentId)}/ack`;
    const init = { method: "POST", headers: { "If-Match": etag }, body: JSON.stringify({ action }) };
    for (let attempt = 1; ; attempt++) {
      if (this.getToken() !== actorToken) throw new Error("La sesión cambió. Vuelve a revisar el incidente.");
      try {
        return await this.request<Incident>(path, init);
      } catch (err) {
        const pending = err instanceof ApiRequestError && err.status === 503 && err.code === "CALLBACK_PENDING";
        const incompleteResponse = err instanceof TypeError || err instanceof SyntaxError;
        const seconds = err instanceof ApiRequestError ? err.retryAfter ?? attempt : attempt;
        if ((!pending && !incompleteResponse) || seconds > 10 || attempt >= ACK_ATTEMPTS) throw err;
        await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
      }
    }
  }
  async injectFault(machineId: string, faultType: FaultType) {
    await this.request<void>(`/simulator/faults`, { method: "POST", body: JSON.stringify({ machineId, faultType }) });
  }
}
