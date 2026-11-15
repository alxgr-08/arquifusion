import { describe, expect, it } from "vitest";
import { sessionFromIdToken } from "./auth";

const NOW = 1_800_000_000;

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(o))))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${b64({ alg: "RS256" })}.${b64(claims)}.signature`;
}

describe("sessionFromIdToken", () => {
  it("reads email, name and expiry from a valid token", () => {
    const token = jwt({ email: "tech@example.com", name: "Técnico Uno", exp: NOW + 3600 });
    expect(sessionFromIdToken(token, NOW)).toEqual({
      idToken: token,
      email: "tech@example.com",
      name: "Técnico Uno",
      expiresAt: NOW + 3600,
    });
  });

  it("rejects expired or about-to-expire tokens", () => {
    expect(sessionFromIdToken(jwt({ email: "a@b.c", exp: NOW - 1 }), NOW)).toBeUndefined();
    expect(sessionFromIdToken(jwt({ email: "a@b.c", exp: NOW + 30 }), NOW)).toBeUndefined();
  });

  it("rejects malformed tokens", () => {
    expect(sessionFromIdToken("not-a-jwt", NOW)).toBeUndefined();
    expect(sessionFromIdToken("a.%%%.c", NOW)).toBeUndefined();
  });
});
