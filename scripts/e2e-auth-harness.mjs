// Everything the sign-in browser check needs, with no AWS and no dependencies (Node 22+):
//
//   * a fake Cognito: /oauth2/authorize, /oauth2/token (checks PKCE), /logout and the signing keys, issuing real
//     RS256 access and ID tokens for whichever test user is "signed in at the hosted page";
//   * the real backend, started with that fake pool as its only trusted issuer, on a throwaway database;
//   * the production site (frontend/out) served the way CloudFront serves it: one origin, /api/* forwarded to the
//     backend, directory-index rewrites, and a generated /auth-config.json.
//
// Used by scripts/e2e-ui.mjs when AUTH=1. Needs `npm run build` in frontend/ first.

import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const ISSUER_PORT = 9099;
const SITE_PORT = 9098;
const API_PORT = 9097;
const POOL_ID = "us-east-1_E2ePool01";
const CLIENT_ID = "e2e-client";
const KID = "e2e-key-1";

export const USERS = {
  alice: { sub: "11111111-1111-4111-8111-111111111111", email: "alice@example.com" },
  bob: { sub: "22222222-2222-4222-8222-222222222222", email: "bob@example.com" },
};

// The same directives as infra/agentops_infra/csp.py (a test compares them), with the fake Cognito as the one extra
// place the page may connect to. The browser test fails on any violation, so a policy that would break the real
// site is caught here.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  `connect-src 'self' http://localhost:${ISSUER_PORT}`,
].join("; ");

const b64 = (buf) => Buffer.from(buf).toString("base64url");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".txt": "text/plain", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2" }; // fmt: skip

function listen(server, port) {
  return new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(port, "127.0.0.1", ok);
  });
}

/** The fake hosted page. `state.user` is who signs in next; `state.issued` counts token exchanges. */
function fakeCognito(state) {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = { ...publicKey.export({ format: "jwk" }), kid: KID, alg: "RS256", use: "sig" };
  const issuer = `http://localhost:${ISSUER_PORT}/pool`;
  const codes = new Map();

  const jwt = (claims) => {
    const head = b64(JSON.stringify({ kid: KID, alg: "RS256" }));
    const body = b64(JSON.stringify(claims));
    return `${head}.${body}.${b64(sign("sha256", Buffer.from(`${head}.${body}`), privateKey))}`;
  };

  return createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${ISSUER_PORT}`);
    const cors = { "Access-Control-Allow-Origin": "*" };
    if (url.pathname === "/pool/.well-known/jwks.json") {
      res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ keys: [jwk] }));
    } else if (url.pathname === "/oauth2/authorize") {
      const q = url.searchParams;
      if (q.get("client_id") !== CLIENT_ID || q.get("response_type") !== "code" || q.get("code_challenge_method") !== "S256") {
        res.writeHead(400).end("bad authorize request");
        return;
      }
      const code = randomBytes(12).toString("hex");
      codes.set(code, { challenge: q.get("code_challenge"), redirect: q.get("redirect_uri"), user: state.user });
      const back = new URL(q.get("redirect_uri"));
      back.searchParams.set("code", code);
      back.searchParams.set("state", q.get("state"));
      res.writeHead(302, { Location: back.href }).end();
    } else if (url.pathname === "/oauth2/token" && req.method === "POST") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const f = new URLSearchParams(raw);
      const entry = codes.get(f.get("code"));
      codes.delete(f.get("code")); // a code works once
      const verifier = f.get("code_verifier") ?? "";
      const ok =
        entry &&
        f.get("grant_type") === "authorization_code" &&
        f.get("client_id") === CLIENT_ID &&
        f.get("redirect_uri") === entry.redirect &&
        createHash("sha256").update(verifier).digest("base64url") === entry.challenge;
      if (!ok) {
        res.writeHead(400, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify({ error: "invalid_grant" }));
        return;
      }
      state.issued += 1;
      const now = Math.floor(Date.now() / 1000);
      const common = { sub: entry.user.sub, iss: issuer, iat: now, exp: now + (state.ttl ?? 3600) };
      const tokens = {
        access_token: jwt({ ...common, client_id: CLIENT_ID, token_use: "access", scope: "openid email" }),
        id_token: jwt({ ...common, aud: CLIENT_ID, token_use: "id", email: entry.user.email }),
        expires_in: state.ttl ?? 3600,
        token_type: "Bearer",
      };
      res.writeHead(200, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify(tokens));
    } else if (url.pathname === "/logout") {
      res.writeHead(302, { Location: url.searchParams.get("logout_uri") ?? "/" }).end();
    } else {
      res.writeHead(404).end();
    }
  });
}

function pythonFor(backendDir) {
  const candidates = [
    process.env.PYTHON,
    join(backendDir, ".venv", "Scripts", "python.exe"),
    join(backendDir, ".venv", "bin", "python"),
  ].filter(Boolean);
  return candidates.find((p) => existsSync(p)) ?? "python";
}

/** The built site, one origin with the API, like CloudFront in front of S3 and API Gateway. */
function site(authConfig) {
  const out = join(ROOT, "frontend", "out");
  return createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${SITE_PORT}`);
    if (url.pathname.startsWith("/api/") || url.pathname === "/health") {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      const body = chunks.length ? Buffer.concat(chunks) : undefined;
      const hop = new Set(["host", "connection", "content-length", "transfer-encoding", "keep-alive"]);
      const headers = Object.fromEntries(Object.entries(req.headers).filter(([k]) => !hop.has(k)));
      try {
        const upstream = await fetch(`http://127.0.0.1:${API_PORT}${url.pathname}${url.search}`, {
          method: req.method,
          headers,
          body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
        });
        const payload = Buffer.from(await upstream.arrayBuffer());
        const back = Object.fromEntries([...upstream.headers].filter(([k]) => !hop.has(k)));
        if (upstream.status === 404) {
          // CloudFront's distribution-wide custom error response: any 404, from the API too, is answered with the
          // site's 404 page. The status survives; the JSON error body does not. The real site behaves this way.
          const page = join(out, "404.html");
          res.writeHead(404, { "Content-Type": "text/html", "Content-Security-Policy": CSP }).end(existsSync(page) ? readFileSync(page) : "not found");
          return;
        }
        res.writeHead(upstream.status, back).end(payload);
      } catch {
        res.writeHead(502).end("backend unreachable");
      }
      return;
    }
    if (url.pathname === "/auth-config.json" && authConfig) {
      res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP }).end(JSON.stringify(authConfig));
      return;
    }
    let path = decodeURIComponent(url.pathname);
    if (path.endsWith("/")) path += "index.html";
    else if (!path.includes(".")) path += "/index.html"; // the CloudFront directory-index function
    const file = normalize(join(out, path));
    if (!file.startsWith(out) || !existsSync(file) || !statSync(file).isFile()) {
      const notFound = join(out, "404.html");
      res.writeHead(404, { "Content-Type": "text/html", "Content-Security-Policy": CSP }).end(existsSync(notFound) ? readFileSync(notFound) : "not found");
      return;
    }
    res.writeHead(200, { "Content-Type": MIME[extname(file)] ?? "application/octet-stream", "Cache-Control": "no-store", "Content-Security-Policy": CSP }).end(readFileSync(file));
  });
}

/** `auth: false` starts the same stack without sign-in (what STACK=1 uses); `backendEnv` adds settings to the API. */
export async function startHarness({ auth = true, backendEnv = {} } = {}) {
  if (!existsSync(join(ROOT, "frontend", "out", "index.html"))) {
    throw new Error("frontend/out is missing: run `npm run build` in frontend/ first.");
  }
  const state = { user: USERS.alice, issued: 0, ttl: 3600 };
  const issuer = fakeCognito(state);
  await listen(issuer, ISSUER_PORT);

  const data = mkdtempSync(join(tmpdir(), "agentops-e2e-auth-"));
  const backendDir = join(ROOT, "backend");
  const api = spawn(
    pythonFor(backendDir),
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
    {
      cwd: backendDir,
      stdio: "ignore",
      env: {
        ...process.env,
        AGENTOPS_NO_DOTENV: "1",
        STORAGE_MODE: "local",
        LOCAL_DATA_DIR: data,
        AGENT_PROVIDER: "mock",
        TELEMETRY_DATASET: "",
        ACCESS_CODE: "",
        ACCESS_SECRET_ARN: "",
        COGNITO_USER_POOL_ID: auth ? POOL_ID : "",
        COGNITO_ISSUER: auth ? `http://localhost:${ISSUER_PORT}/pool` : "",
        ...backendEnv,
      },
    },
  );
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${API_PORT}/health`)).ok) break;
    } catch {
      /* still starting */
    }
    if (i > 100) throw new Error("The backend did not start.");
    await new Promise((r) => setTimeout(r, 200));
  }

  const web = site(
    auth
      ? { region: "us-east-1", userPoolId: POOL_ID, clientId: CLIENT_ID, hostedUiUrl: `http://localhost:${ISSUER_PORT}` }
      : null,
  );
  await listen(web, SITE_PORT);

  return {
    state,
    siteUrl: `http://localhost:${SITE_PORT}`,
    async stop() {
      web.close();
      issuer.close();
      api.kill();
      await new Promise((r) => setTimeout(r, 500));
      try {
        rmSync(data, { recursive: true, force: true });
      } catch {
        /* the database file may still be closing */
      }
    },
  };
}
