/** Google Identity Services sign-in. The ID token lives only in memory and goes to API Gateway as a Bearer JWT. */

const GIS_SRC = "https://accounts.google.com/gsi/client";
const EXPIRY_MARGIN_S = 60;

export const GOOGLE_CLIENT_ID: string = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";

export interface Session {
  idToken: string;
  email: string;
  name: string;
  expiresAt: number; // epoch seconds
}

interface GoogleIdApi {
  initialize(config: { client_id: string; callback: (r: { credential: string }) => void; auto_select?: boolean }): void;
  renderButton(parent: HTMLElement, options: Record<string, string | number>): void;
  disableAutoSelect(): void;
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleIdApi } };
  }
}

function decodePayload(jwt: string): Record<string, unknown> {
  const part = jwt.split(".")[1];
  if (!part) throw new Error("Invalid JWT");
  const base64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

/** Reads the Google ID token claims the UI needs. Signature is verified by API Gateway, not here. */
export function sessionFromIdToken(idToken: string, nowS = Date.now() / 1000): Session | undefined {
  try {
    const claims = decodePayload(idToken);
    const expiresAt = Number(claims.exp);
    if (!Number.isFinite(expiresAt) || expiresAt - EXPIRY_MARGIN_S <= nowS) return undefined;
    if (GOOGLE_CLIENT_ID && claims.aud !== GOOGLE_CLIENT_ID) return undefined;
    return {
      idToken,
      email: String(claims.email ?? ""),
      name: String(claims.name ?? claims.email ?? ""),
      expiresAt,
    };
  } catch {
    return undefined;
  }
}

let loading: Promise<GoogleIdApi> | undefined;

function loadGis(): Promise<GoogleIdApi> {
  loading ??= new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve(window.google.accounts.id);
    const script = document.createElement("script");
    script.src = GIS_SRC;
    script.async = true;
    script.onload = () => (window.google ? resolve(window.google.accounts.id) : reject(new Error("GIS not available")));
    script.onerror = () => reject(new Error("Could not load Google Identity Services"));
    document.head.appendChild(script);
  });
  return loading;
}

/** Renders the "Sign in with Google" button; `onSession` receives each new valid session. */
export async function renderGoogleButton(parent: HTMLElement, onSession: (s: Session) => void): Promise<void> {
  if (!GOOGLE_CLIENT_ID) throw new Error("VITE_GOOGLE_CLIENT_ID is not set");
  const gis = await loadGis();
  gis.initialize({
    client_id: GOOGLE_CLIENT_ID,
    auto_select: false,
    callback: ({ credential }) => {
      const session = sessionFromIdToken(credential);
      if (session) onSession(session);
    },
  });
  gis.renderButton(parent, { theme: "filled_black", size: "large", text: "signin_with", locale: "es" });
}

export function signOutGoogle(): void {
  window.google?.accounts?.id.disableAutoSelect();
}
