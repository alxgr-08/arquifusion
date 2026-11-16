import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const NOW = 1_800_000_000;
const CLIENT_ID = "test-web-client.apps.googleusercontent.com";
let sessionFromIdToken: typeof import("./auth")["sessionFromIdToken"];

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(o))))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${b64({ alg: "RS256" })}.${b64(claims)}.signature`;
}

describe("sessionFromIdToken", () => {
  beforeEach(async () => {
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", CLIENT_ID);
    vi.resetModules();
    ({ sessionFromIdToken } = await import("./auth"));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("reads email, name and expiry from a valid token", () => {
    const token = jwt({ email: "tech@example.com", name: "Técnico Uno", exp: NOW + 3600, aud: CLIENT_ID });
    expect(sessionFromIdToken(token, NOW)).toEqual({
      idToken: token,
      email: "tech@example.com",
      name: "Técnico Uno",
      expiresAt: NOW + 3600,
    });
  });

  it("rejects a token without the configured client audience", () => {
    expect(sessionFromIdToken(jwt({ email: "a@b.c", exp: NOW + 3600 }), NOW)).toBeUndefined();
  });

  it("rejects a token issued for another client", () => {
    const token = jwt({ email: "a@b.c", exp: NOW + 3600, aud: "another-client.apps.googleusercontent.com" });
    expect(sessionFromIdToken(token, NOW)).toBeUndefined();
  });

  it("rejects expired or about-to-expire tokens", () => {
    for (const exp of [NOW - 1, NOW + 30, NOW + 60]) {
      expect(sessionFromIdToken(jwt({ email: "a@b.c", exp, aud: CLIENT_ID }), NOW)).toBeUndefined();
    }
  });

  it("rejects malformed tokens", () => {
    expect(sessionFromIdToken("not-a-jwt", NOW)).toBeUndefined();
    expect(sessionFromIdToken("a.%%%.c", NOW)).toBeUndefined();
  });
});

describe("session across reloads", () => {
  let auth: typeof import("./auth");
  const store = new Map<string, string>();

  beforeEach(async () => {
    store.clear();
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    });
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", CLIENT_ID);
    vi.resetModules();
    auth = await import("./auth");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("restores a saved, still valid session", () => {
    const token = jwt({ email: "tech@example.com", exp: NOW + 3600, aud: CLIENT_ID });
    auth.persistSession(auth.sessionFromIdToken(token, NOW));
    expect(auth.restoreSession(NOW)?.email).toBe("tech@example.com");
  });

  it("drops an expired saved session", () => {
    const token = jwt({ email: "tech@example.com", exp: NOW + 3600, aud: CLIENT_ID });
    auth.persistSession(auth.sessionFromIdToken(token, NOW));
    expect(auth.restoreSession(NOW + 3600)).toBeUndefined();
    expect(store.size).toBe(0);
  });

  it("forgets the session on sign-out", () => {
    const token = jwt({ email: "tech@example.com", exp: NOW + 3600, aud: CLIENT_ID });
    auth.persistSession(auth.sessionFromIdToken(token, NOW));
    vi.stubGlobal("window", {});
    auth.signOutGoogle();
    expect(auth.restoreSession(NOW)).toBeUndefined();
  });
});
