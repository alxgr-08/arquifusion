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

describe("sign-out with a restored session", () => {
  const store = new Map<string, string>();
  let auth: typeof import("./auth");

  beforeEach(async () => {
    store.clear();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    vi.stubGlobal("window", {});
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", CLIENT_ID);
    vi.spyOn(Date, "now").mockReturnValue(NOW * 1000);
    vi.resetModules();
    auth = await import("./auth");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  function installGis() {
    const gis = {
      initialize: vi.fn(),
      renderButton: vi.fn(),
      prompt: vi.fn(),
      disableAutoSelect: vi.fn(),
    };
    vi.stubGlobal("window", { google: { accounts: { id: gis } } });
    return gis;
  }

  async function restoreThenSignOut() {
    const token = jwt({ email: "tech@example.com", exp: NOW + 3600, aud: CLIENT_ID });
    auth.persistSession(auth.sessionFromIdToken(token, NOW));
    vi.resetModules();
    auth = await import("./auth");
    expect(auth.restoreSession(NOW)?.email).toBe("tech@example.com");
    auth.signOutGoogle(); // GIS is absent after restoring the session on F5.
  }

  it("keeps the login screen after F5 then sign-out before GIS has loaded", async () => {
    await restoreThenSignOut();
    const gis = installGis();
    await auth.renderGoogleButton({} as HTMLDivElement, vi.fn());
    expect(gis.initialize).toHaveBeenCalledWith(expect.objectContaining({ auto_select: false }));
    expect(gis.disableAutoSelect).toHaveBeenCalledOnce();
    expect(gis.renderButton).toHaveBeenCalledOnce();
    expect(gis.prompt).not.toHaveBeenCalled();
    expect(auth.restoreSession(NOW)).toBeUndefined();
  });

  it("stays signed out after another reload of the same tab", async () => {
    await restoreThenSignOut();
    vi.resetModules();
    auth = await import("./auth");
    const gis = installGis();
    await auth.renderGoogleButton({} as HTMLDivElement, vi.fn());
    expect(gis.initialize).toHaveBeenCalledWith(expect.objectContaining({ auto_select: false }));
    expect(gis.prompt).not.toHaveBeenCalled();
    expect(auth.restoreSession(NOW)).toBeUndefined();
  });

  it("allows a manual sign-in after sign-out and restores that session on F5", async () => {
    await restoreThenSignOut();
    const gis = installGis();
    const onSession = vi.fn((session) => auth.persistSession(session));
    await auth.renderGoogleButton({} as HTMLDivElement, onSession);
    const token = jwt({ email: "another@example.com", exp: NOW + 3600, aud: CLIENT_ID });
    gis.initialize.mock.calls[0][0].callback({ credential: token });
    expect(onSession).toHaveBeenCalledOnce();
    vi.resetModules();
    auth = await import("./auth");
    expect(auth.restoreSession(NOW)?.email).toBe("another@example.com");
  });

  it("preserves One Tap for a returning user who has not signed out", async () => {
    const gis = installGis();
    await auth.renderGoogleButton({} as HTMLDivElement, vi.fn());
    expect(gis.initialize).toHaveBeenCalledWith(expect.objectContaining({ auto_select: true }));
    expect(gis.prompt).toHaveBeenCalledOnce();
    expect(gis.disableAutoSelect).not.toHaveBeenCalled();
  });

  it("keeps sign-out effective in memory when browser storage is blocked", async () => {
    const blocked = () => { throw new Error("Storage blocked"); };
    vi.stubGlobal("sessionStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
    auth.signOutGoogle();
    const gis = installGis();
    await auth.renderGoogleButton({} as HTMLDivElement, vi.fn());
    expect(gis.initialize).toHaveBeenCalledWith(expect.objectContaining({ auto_select: false }));
    expect(gis.prompt).not.toHaveBeenCalled();
    expect(auth.restoreSession(NOW)).toBeUndefined();
  });
});
