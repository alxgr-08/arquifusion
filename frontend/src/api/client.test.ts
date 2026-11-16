import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, errorMessage, HttpFleetApi, RateLimiter } from "./client";

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("HttpFleetApi", () => {
  it("repeats the same ack while the API answers 503 CALLBACK_PENDING", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(reply(503, { error: { code: "CALLBACK_PENDING", message: "repeat" } }))
      .mockResolvedValueOnce(reply(200, { incidentId: "INC-1", state: "IN_PROGRESS" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await new HttpFleetApi("https://api.test/dev", () => "tok").ackIncident("EXC-0042", "INC-1", "ARRIVED", '"v1"');

    expect(result).toMatchObject({ incidentId: "INC-1" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe("https://api.test/dev/machines/EXC-0042/incidents/INC-1/ack");
      expect(init.body).toBe(JSON.stringify({ action: "ARRIVED" }));
      expect(init.headers.Authorization).toBe("Bearer tok");
      expect(init.headers["If-Match"]).toBe('"v1"');
    }
  });

  it("clears the session on 401 and does not retry other errors", async () => {
    const onUnauthorized = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(401, { error: { code: "UNAUTHORIZED", message: "no" } })));
    const api = new HttpFleetApi("https://api.test/dev", () => "tok", onUnauthorized);

    await expect(api.ackIncident("EXC-0042", "INC-1", "RESOLVED", '"v1"')).rejects.toBeInstanceOf(ApiRequestError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("reads the quoted ETag and requires a new explicit decision when its wait expires", async () => {
    const detail = new Response(JSON.stringify({ incidentId: "INC-1", state: "ASSIGNED" }), { headers: { ETag: '"wait1"' } });
    const network = vi.fn().mockResolvedValueOnce(detail)
      .mockResolvedValueOnce(reply(412, { error: { code: "STALE_WAIT", message: "changed" } }));
    vi.stubGlobal("fetch", network);
    const api = new HttpFleetApi("https://api.test/dev", () => "tok");
    const view = await api.getIncident("EXC-0042", "INC-1");
    expect(view.etag).toBe('"wait1"');
    await expect(api.ackIncident("EXC-0042", "INC-1", "ARRIVED", view.etag!)).rejects.toMatchObject({ status: 412 });
    expect(network).toHaveBeenCalledTimes(2);
    expect(network.mock.calls[1][1].headers["If-Match"]).toBe(view.etag);
  });

  it("uses the identical body and reviewed wait after a lost HTTP response", async () => {
    const network = vi.fn().mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(reply(200, { incidentId: "INC-1" }));
    vi.stubGlobal("fetch", network);
    await new HttpFleetApi("https://api.test/dev", () => "tok").ackIncident("EXC-0042", "INC-1", "RESOLVED", '"wait2"');
    expect(network.mock.calls[1]).toEqual(network.mock.calls[0]);
  });

  it("rejects missing or weak validators without sending a callback", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const api = new HttpFleetApi("https://api.test/dev", () => "tok");
    for (const tag of ["", 'W/"weak"', "*"]) {
      await expect(api.ackIncident("M", "I", "ARRIVED", tag)).rejects.toThrow("Revisa");
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("recovers a truncated successful JSON body with the identical reviewed command", async () => {
    vi.useFakeTimers();
    try {
      const network = vi.fn().mockResolvedValueOnce(new Response('{"incidentId":', { status: 200 }))
        .mockResolvedValueOnce(reply(200, { incidentId: "INC-1", state: "IN_PROGRESS" }));
      vi.stubGlobal("fetch", network);
      const pending = new HttpFleetApi("https://api.test/dev", () => "tok")
        .ackIncident("EXC-0042", "INC-1", "ARRIVED", '"wait3"');
      await vi.runAllTimersAsync();
      await expect(pending).resolves.toMatchObject({ incidentId: "INC-1", state: "IN_PROGRESS" });
      expect(network).toHaveBeenCalledTimes(2);
      expect(network.mock.calls[1]).toEqual(network.mock.calls[0]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries a throttled read and explains gateway errors that carry no statusText", async () => {
    vi.useFakeTimers();
    try {
      const throttled = () => new Response(JSON.stringify({ message: "Too Many Requests" }), { status: 429 });
      const network = vi.fn().mockResolvedValueOnce(throttled()).mockResolvedValueOnce(reply(200, [{ machineId: "M" }]));
      vi.stubGlobal("fetch", network);
      const machines = new HttpFleetApi("https://api.test/dev", () => "tok").listMachines("OP");
      await vi.runAllTimersAsync();
      await expect(machines).resolves.toEqual([{ machineId: "M" }]);
      expect(network).toHaveBeenCalledTimes(2);

      network.mockReset().mockImplementation(async () => throttled());
      const failing = new HttpFleetApi("https://api.test/dev", () => "tok").listMachines("OP");
      const assertion = expect(failing).rejects.toMatchObject({ status: 429, code: "THROTTLED", message: "Too Many Requests" });
      await vi.runAllTimersAsync();
      await assertion;
      expect(network).toHaveBeenCalledTimes(3);
      expect(errorMessage(new ApiRequestError(429, "THROTTLED", ""))).toContain("limitando");
      expect(errorMessage(new ApiRequestError(503, "HTTP_ERROR", ""))).toContain("HTTP 503");
      expect(errorMessage(new TypeError("Failed to fetch"))).toContain("conectar");
    } finally {
      vi.useRealTimers();
    }
  });

  it("repeats the identical ack after a gateway throttle or Lambda concurrency limit", async () => {
    vi.useFakeTimers();
    try {
      const network = vi.fn()
        .mockResolvedValueOnce(new Response(JSON.stringify({ message: "Too Many Requests" }), { status: 429 }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ message: "Service Unavailable" }), { status: 503 }))
        .mockResolvedValueOnce(reply(200, { incidentId: "INC-1", state: "IN_PROGRESS" }));
      vi.stubGlobal("fetch", network);
      const pending = new HttpFleetApi("https://api.test/dev", () => "tok")
        .ackIncident("EXC-0042", "INC-1", "ARRIVED", '"wait4"');
      await vi.runAllTimersAsync();
      await expect(pending).resolves.toMatchObject({ state: "IN_PROGRESS" });
      expect(network).toHaveBeenCalledTimes(3);
      expect(network.mock.calls[2]).toEqual(network.mock.calls[0]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("spaces requests beyond the burst to stay under the stage rate", async () => {
    vi.useFakeTimers();
    try {
      const network = vi.fn().mockImplementation(async () => reply(200, []));
      vi.stubGlobal("fetch", network);
      const api = new HttpFleetApi("https://api.test/dev", () => "tok", undefined, new RateLimiter(1, 3));
      const all = Promise.all(Array.from({ length: 5 }, () => api.listMachines("OP")));
      await vi.advanceTimersByTimeAsync(0);
      expect(network).toHaveBeenCalledTimes(3);
      await vi.advanceTimersByTimeAsync(1000);
      expect(network).toHaveBeenCalledTimes(4);
      await vi.advanceTimersByTimeAsync(1000);
      await all;
      expect(network).toHaveBeenCalledTimes(5);
    } finally {
      vi.useRealTimers();
    }
  });

  it("honors Retry-After and stops a retry if the signed-in identity changes", async () => {
    vi.useFakeTimers();
    try {
      let token = "first";
      const response = new Response(JSON.stringify({ error: { code: "CALLBACK_PENDING", message: "pending" } }), {
        status: 503, headers: { "Retry-After": "2", "Content-Type": "application/json" },
      });
      const network = vi.fn().mockResolvedValue(response);
      vi.stubGlobal("fetch", network);
      const pending = new HttpFleetApi("https://api.test/dev", () => token)
        .ackIncident("EXC-0042", "INC-1", "ARRIVED", '"wait3"');
      const assertion = expect(pending).rejects.toThrow("La sesión cambió");
      await vi.advanceTimersByTimeAsync(1999);
      expect(network).toHaveBeenCalledOnce();
      token = "second";
      await vi.advanceTimersByTimeAsync(1);
      await assertion;
      expect(network).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });
});
