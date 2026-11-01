// Sign-in with Amazon Cognito's hosted page: authorization code + PKCE. No SDK and no client secret (a browser
// cannot keep one; the one-time PKCE verifier protects the code instead).
//
// The access token is kept in this tab's sessionStorage and sent to the API as a bearer token. It is gone when
// the tab closes and expires after an hour; the site never asks for a refresh token. Anything stored in the
// browser can be read by script running on the page, so nothing here is more than a demo-grade secret: the
// token can start investigations as you, and nothing else (no AWS access, no admin scope).
//
// Whether sign-in is on is decided by the API (`/api/config`). How to reach Cognito comes from
// `/auth-config.json`, written next to the site at deploy time, so the static build knows nothing about it.

export interface AuthConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  hostedUiUrl: string;
}

export interface Session {
  accessToken: string;
  idToken: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

const SESSION_KEY = "agentops-session";
const PKCE_KEY = "agentops-pkce";
const SKEW_MS = 30_000; // treat a token as expired slightly early rather than send one that dies in flight

/** Raised with wording safe to show a person. */
export class SignInError extends Error {}

// ---- configuration ---------------------------------------------------------------------------------

let configPromise: Promise<AuthConfig | null> | null = null;

function isHostedUiUrl(url: string): boolean {
  return /^https:\/\/[A-Za-z0-9.-]+$/.test(url) || /^http:\/\/localhost(:\d+)?$/.test(url);
}

/** The site's sign-in settings, or null when this deployment has none (or they are unusable). */
export function loadAuthConfig(): Promise<AuthConfig | null> {
  configPromise ??= (async () => {
    try {
      const res = await fetch("/auth-config.json", { cache: "no-store" });
      if (!res.ok) return null;
      const body = (await res.json()) as Partial<Record<keyof AuthConfig, unknown>>;
      const { region, userPoolId, clientId, hostedUiUrl } = body;
      if (
        typeof region !== "string" ||
        typeof userPoolId !== "string" ||
        typeof clientId !== "string" ||
        typeof hostedUiUrl !== "string" ||
        !clientId ||
        !isHostedUiUrl(hostedUiUrl)
      ) {
        return null;
      }
      return { region, userPoolId, clientId, hostedUiUrl };
    } catch {
      return null;
    }
  })();
  return configPromise;
}

// ---- session ---------------------------------------------------------------------------------------

const listeners = new Set<() => void>();

/** Run `fn` whenever the session starts or ends in this tab. Returns the unsubscribe function. */
export function subscribeSession(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function emit(): void {
  listeners.forEach((fn) => fn());
}

/** The current session, or null if there is none or it has (nearly) expired. */
export function getSession(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Session>;
    if (typeof s.accessToken !== "string" || typeof s.idToken !== "string" || typeof s.expiresAt !== "number") {
      return null;
    }
    return s.expiresAt - SKEW_MS > Date.now() ? (s as Session) : null;
  } catch {
    return null;
  }
}

function saveSession(session: Session): void {
  try {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    throw new SignInError("This browser blocked storing your sign-in. Allow site data for this page and try again.");
  }
  emit();
}

export function clearSession(): void {
  try {
    window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* nothing stored, nothing to clear */
  }
  emit();
}

function base64UrlToText(part: string): string {
  const std = part.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(std.padEnd(Math.ceil(std.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** The email in the ID token, for display only. The server never trusts this; it verifies the access token. */
export function sessionEmail(session: Session): string | null {
  try {
    const payload = JSON.parse(base64UrlToText(session.idToken.split(".")[1] ?? "")) as { email?: unknown };
    return typeof payload.email === "string" ? payload.email : null;
  } catch {
    return null;
  }
}

// ---- the sign-in round trip ------------------------------------------------------------------------

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(byteLength: number): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

/** Only ever send people back to a path on this site. */
export function safeReturnTo(path: string | null | undefined): string {
  return path && /^\/(?![/\\])[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]*$/.test(path) ? path : "/";
}

/** An address on Cognito's hosted page (another origin, so a URL object keeps that explicit). */
function hostedUrl(config: AuthConfig, path: string, query: URLSearchParams): URL {
  const url = new URL(path, config.hostedUiUrl);
  url.search = query.toString();
  return url;
}

const redirectUri = () => `${window.location.origin}/auth/callback`;

/** Send the browser to Cognito's sign-in page; it comes back to /auth/callback and then to `returnTo`. */
export async function signIn(returnTo: string): Promise<void> {
  const config = await loadAuthConfig();
  if (!config) throw new SignInError("Sign-in is not set up on this site.");
  const verifier = randomString(48);
  const state = randomString(16);
  const challenge = base64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  try {
    window.sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state, returnTo: safeReturnTo(returnTo) }));
  } catch {
    throw new SignInError("This browser blocked storing your sign-in. Allow site data for this page and try again.");
  }
  const query = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri(),
    scope: "openid email",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(hostedUrl(config, "/oauth2/authorize", query));
}

interface Pending {
  verifier: string;
  state: string;
  returnTo: string;
}

function takePending(): Pending | null {
  try {
    const raw = window.sessionStorage.getItem(PKCE_KEY);
    window.sessionStorage.removeItem(PKCE_KEY); // one use only, whatever happens next
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<Pending>;
    return typeof p.verifier === "string" && typeof p.state === "string" && typeof p.returnTo === "string"
      ? { verifier: p.verifier, state: p.state, returnTo: safeReturnTo(p.returnTo) }
      : null;
  } catch {
    return null;
  }
}

/** Finish sign-in on the callback page: check `state`, swap the code for tokens, store them. Returns where to go next. */
export async function completeSignIn(search: string): Promise<string> {
  const params = new URLSearchParams(search);
  const pending = takePending();
  if (params.get("error")) {
    throw new SignInError(
      params.get("error") === "access_denied" ? "Sign-in was cancelled." : "The sign-in page reported a problem.",
    );
  }
  const code = params.get("code");
  if (!code || !pending || params.get("state") !== pending.state) {
    throw new SignInError("This sign-in link is not valid or was already used. Start again.");
  }
  const config = await loadAuthConfig();
  if (!config) throw new SignInError("Sign-in is not set up on this site.");

  let res: Response;
  try {
    res = await fetch(`${config.hostedUiUrl}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        code,
        redirect_uri: redirectUri(),
        code_verifier: pending.verifier,
      }),
    });
  } catch {
    throw new SignInError("Could not reach the sign-in service. Check your connection and try again.");
  }
  if (!res.ok) throw new SignInError("Sign-in could not be completed. Start again.");
  const body = (await res.json()) as { access_token?: unknown; id_token?: unknown; expires_in?: unknown };
  if (typeof body.access_token !== "string" || typeof body.id_token !== "string" || typeof body.expires_in !== "number") {
    throw new SignInError("The sign-in service sent an unexpected answer. Start again.");
  }
  saveSession({
    accessToken: body.access_token,
    idToken: body.id_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  });
  return pending.returnTo;
}

/** End this tab's session, then let Cognito end its own so the next visit asks for a password again. */
export async function signOut(): Promise<void> {
  clearSession();
  const config = await loadAuthConfig();
  if (!config) return;
  const query = new URLSearchParams({ client_id: config.clientId, logout_uri: `${window.location.origin}/` });
  window.location.assign(hostedUrl(config, "/logout", query));
}
