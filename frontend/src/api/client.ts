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
  listMachines(operationId: string, status?: MachineStatus): Promise<Machine[]>;
  getMachine(machineId: string): Promise<Machine>;
  getReadings(machineId: string, from: string, to: string): Promise<Reading[]>;
  getMachineIncidents(machineId: string): Promise<Incident[]>;
  getMaintenance(machineId: string): Promise<Maintenance[]>;
  listIncidents(operationId: string, state: "OPEN" | "CLOSED"): Promise<Incident[]>;
  listTechnicians(operationId: string): Promise<Technician[]>;
  listParts(): Promise<PartStock[]>;
  getKpis(operationId: string, date: string): Promise<MachineKpi[]>;
  ackIncident(machineId: string, incidentId: string, action: "ARRIVED" | "RESOLVED"): Promise<Incident>;
  injectFault(machineId: string, faultType: FaultType): Promise<void>;
}

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** API Gateway client (contracts/api.md). Token = Cognito JWT. */
export class HttpFleetApi implements FleetApi {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | undefined,
  ) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = this.getToken();
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    const body = response.status === 204 ? undefined : await response.json();
    if (!response.ok) {
      const err = body?.error ?? { code: "HTTP_ERROR", message: response.statusText };
      throw new ApiRequestError(response.status, err.code, err.message);
    }
    return body as T;
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
  listParts() {
    return this.request<PartStock[]>(`/parts`);
  }
  getKpis(op: string, date: string) {
    return this.request<MachineKpi[]>(`/operations/${op}/kpis?date=${date}`);
  }
  ackIncident(machineId: string, incidentId: string, action: "ARRIVED" | "RESOLVED") {
    return this.request<Incident>(`/machines/${machineId}/incidents/${incidentId}/ack`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
  }
  async injectFault(machineId: string, faultType: FaultType) {
    await this.request<void>(`/simulator/faults`, { method: "POST", body: JSON.stringify({ machineId, faultType }) });
  }
}
