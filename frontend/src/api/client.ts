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
const READ_ATTEMPTS = 3;
/** Gateway answers that carry no effect: stage throttle (429), Lambda concurrency limit or timeout (502-504). */
const TRANSIENT_STATUS = new Set([429, 502, 503, 504]);

const sleep = (seconds: number) => new Promise((resolve) => setTimeout(resolve, seconds * 1000));

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

const ERROR_TEXT: Record<string, string> = {
  NO_GRANT: "Tu cuenta no tiene acceso a esta operación (falta su grant).",
  OTHER_OPERATION: "Este recurso es de otra operación.",
  FORBIDDEN: "Tu rol no tiene permiso para esta acción o sección.",
  NOT_ASSIGNED: "Solo el técnico asignado o el jefe de mantenimiento puede confirmar.",
  NO_TECH_ID: "Tu cuenta no está vinculada a un técnico.",
  GRANT_CHANGED: "Tus permisos cambiaron; vuelve a intentarlo.",
  CALLBACK_PENDING: "La confirmación quedó guardada; inténtalo de nuevo en unos segundos.",
  STALE_WAIT: "La espera cambió. Revisa la asignación y confirma de nuevo.",
  TASK_EXPIRED: "La espera venció. Consulta la asignación actual.",
  WAIT_NEEDS_RECONCILIATION: "La espera necesita revisión de mantenimiento.",
  WRONG_STAGE: "El incidente ya cambió de etapa. Actualiza el detalle.",
  ACK_CONFLICT: "Esta espera ya se confirmó con otros datos.",
  ACK_BY_OTHER: "Otra persona ya confirmó esta espera.",
  PRECONDITION_REQUIRED: "Revisa la espera antes de confirmar.",
};

/** Human-readable text for any API or network failure. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (ERROR_TEXT[err.code]) return ERROR_TEXT[err.code];
    if (err.status === 401) return "Tu sesión expiró. Inicia sesión de nuevo.";
    if (err.status === 429) return "La API está limitando solicitudes; se reintentará en unos segundos.";
    if (err.status >= 500) return `El servicio no respondió (HTTP ${err.status}). Reintenta en unos segundos.`;
    return err.message || `Error HTTP ${err.status}`;
  }
  if (err instanceof TypeError) return "No se pudo conectar con la API. Revisa tu conexión y reintenta.";
  return err instanceof Error ? err.message || "Error desconocido" : String(err);
}

/** Token bucket per tab, below the API stage throttle (1 req/s, burst 4) so page loads do not trip it. */
export class RateLimiter {
  private tokens: number;
  private last = Date.now();
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly perSecond = 1, private readonly burst = 3) {
    this.tokens = burst;
  }

  take(): Promise<void> {
    const turn = this.queue.then(async () => {
      const now = Date.now();
      this.tokens = Math.min(this.burst, this.tokens + ((now - this.last) / 1000) * this.perSecond);
      this.last = now;
      if (this.tokens < 1) {
        await sleep((1 - this.tokens) / this.perSecond);
        this.tokens = 1;
        this.last = Date.now();
      }
      this.tokens -= 1;
    });
    this.queue = turn;
    return turn;
  }
}

/** API Gateway client (contracts/api.md). Token = Google ID token (JWT authorizer). */
export class HttpFleetApi implements FleetApi {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => string | undefined,
    private readonly onUnauthorized?: () => void,
    private readonly limiter = new RateLimiter(),
  ) {}

  private async response(path: string, init?: RequestInit): Promise<Response> {
    await this.limiter.take();
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
      // API Gateway's own answers (401, 429, 503...) are {"message": ...}; over HTTP/2 statusText is empty.
      const err = body?.error ?? {
        code: response.status === 429 ? "THROTTLED" : "HTTP_ERROR",
        message: body?.message || response.statusText || `HTTP ${response.status}`,
      };
      const delay = response.headers.get("Retry-After");
      throw new ApiRequestError(response.status, err.code, err.message, delay && /^\d+$/.test(delay) ? Number(delay) : null);
    }
    return response;
  }

  /** GETs are safe to repeat: retry gateway throttles, timeouts and lost connections a couple of times. */
  private async read(path: string): Promise<Response> {
    for (let attempt = 1; ; attempt++) {
      try {
        return await this.response(path);
      } catch (err) {
        const transient = err instanceof TypeError || (err instanceof ApiRequestError && TRANSIENT_STATUS.has(err.status));
        if (!transient || attempt >= READ_ATTEMPTS) throw err;
        const retryAfter = err instanceof ApiRequestError ? err.retryAfter : null;
        await sleep(Math.min(retryAfter ?? attempt * 1.5, 5));
      }
    }
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = init?.method && init.method !== "GET" ? await this.response(path, init) : await this.read(path);
    return response.status === 204 ? undefined as T : response.json();
  }

  async getIncident(machineId: string, incidentId: string) {
    const response = await this.read(`/machines/${encodeURIComponent(machineId)}/incidents/${encodeURIComponent(incidentId)}`);
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
        // Same If-Match and body: the API answers a repeated identical ack with the same single effect.
        const pending = err instanceof ApiRequestError && TRANSIENT_STATUS.has(err.status);
        const incompleteResponse = err instanceof TypeError || err instanceof SyntaxError;
        const seconds = err instanceof ApiRequestError ? err.retryAfter ?? attempt : attempt;
        if ((!pending && !incompleteResponse) || seconds > 10 || attempt >= ACK_ATTEMPTS) throw err;
        await sleep(seconds);
      }
    }
  }
  async injectFault(machineId: string, faultType: FaultType) {
    await this.request<void>(`/simulator/faults`, { method: "POST", body: JSON.stringify({ machineId, faultType }) });
  }
}
