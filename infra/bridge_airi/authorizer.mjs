import { JwtVerifier } from "aws-jwt-verify";

const verify = JwtVerifier.create({
  issuer: "https://accounts.google.com",
  audience: process.env.AUDIENCE,
  jwksUri: "https://www.googleapis.com/oauth2/v3/certs",
});

export async function handler(event) {
  const token = /^Bearer ([A-Za-z0-9_.-]+)$/.exec(event.authorizationToken ?? "")?.[1];
  if (!token || !process.env.GOOGLE_SUB) throw Error("Unauthorized");
  try {
    const claims = await verify.verify(token, {
      customJwtCheck: ({ header }) => {
        if (header.alg !== "RS256") throw Error("Unauthorized");
      },
    });
    if (claims.sub !== process.env.GOOGLE_SUB || claims.exp < Date.now() / 1000 + 65) {
      throw Error("Unauthorized");
    }
    const [api, stage] = event.methodArn.split("/");
    return {
      principalId: claims.sub,
      policyDocument: {
        Version: "2012-10-17",
        Statement: [{
          Action: "execute-api:Invoke", Effect: "Allow",
          Resource: ["/POST/lease", "/POST/complete", "/GET/files/*", "/POST/kpi", "/POST/status"]
            .map(path => api + "/" + stage + path),
        }],
      },
    };
  } catch {
    throw Error("Unauthorized");
  }
}
