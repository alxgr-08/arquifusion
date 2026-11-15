import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, HttpFleetApi } from "./client";

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

    const result = await new HttpFleetApi("https://api.test/dev", () => "tok").ackIncident("EXC-0042", "INC-1", "ARRIVED");

    expect(result).toMatchObject({ incidentId: "INC-1" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe("https://api.test/dev/machines/EXC-0042/incidents/INC-1/ack");
      expect(init.body).toBe(JSON.stringify({ action: "ARRIVED" }));
      expect(init.headers.Authorization).toBe("Bearer tok");
    }
  });

  it("clears the session on 401 and does not retry other errors", async () => {
    const onUnauthorized = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(401, { error: { code: "UNAUTHORIZED", message: "no" } })));
    const api = new HttpFleetApi("https://api.test/dev", () => "tok", onUnauthorized);

    await expect(api.ackIncident("EXC-0042", "INC-1", "RESOLVED")).rejects.toBeInstanceOf(ApiRequestError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledOnce();
  });
});
