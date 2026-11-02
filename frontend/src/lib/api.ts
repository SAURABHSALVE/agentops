import { clearSession, getSession } from "./auth";
import type { Incident, IncidentSummary, Investigation, TimelineEvent } from "./types";

// Local development talks to the API on :8000. The deployed static site shares an origin with the API
// (CloudFront routes /api/* to it), so the base is empty there. Public config only: never a secret.
const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
export const API_BASE = (configured ?? (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "")).replace(
  /\/$/,
  "",
);

const ACCESS_KEY = "agentops-access-code";

/** The access code lives only in this tab's sessionStorage (cleared when the tab closes). */
export function getAccessCode(): string {
  try {
    return window.sessionStorage.getItem(ACCESS_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAccessCode(code: string): void {
  try {
    if (code) window.sessionStorage.setItem(ACCESS_KEY, code);
    else window.sessionStorage.removeItem(ACCESS_KEY);
  } catch {
    /* storage unavailable: the code simply is not remembered */
  }
}

export interface PublicConfig {
  data_mode: "simulated" | "captured";
  write_access: "open" | "code_required";
  /** Absent on an API older than sign-in. */
  auth?: { mode: "none" | "cognito" };
  provider: { kind: "mock" | "openai"; label: string };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  let res: Response;
  const token = getSession()?.accessToken;
  try {
    const headers: Record<string, string> = { Accept: "application/json", ...(init?.headers as Record<string, string>) };
    const code = init?.method === "POST" ? getAccessCode() : "";
    if (code) headers["X-Access-Code"] = code;
    if (token) headers["Authorization"] = `Bearer ${token}`;
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(
      `Cannot reach the API${API_BASE ? ` at ${API_BASE}` : ""}. Is the backend running?`,
      0,
      "network_error",
    );
  }
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    let code = "http_error";
    try {
      const body = (await res.json()) as { error?: { code?: string; message?: string } };
      if (body.error?.message) message = body.error.message;
      if (body.error?.code) code = body.error.code;
    } catch {
      /* keep the generic message */
    }
    if (res.status === 401 && code === "invalid_token" && token) {
      // The server no longer accepts this sign-in (expired, or from another deployment). Drop it, and for a read
      // just ask again as a visitor so the page still loads; an action reports "sign in again".
      clearSession();
      if (!retried && (init?.method ?? "GET") === "GET") return request<T>(path, init, true);
    }
    throw new ApiError(message, res.status, code);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  config: (signal?: AbortSignal) => request<PublicConfig>("/api/config", { signal }),
  listIncidents: (signal?: AbortSignal) => request<IncidentSummary[]>("/api/incidents", { signal }),
  getIncident: (id: string, signal?: AbortSignal) => request<Incident>(`/api/incidents/${enc(id)}`, { signal }),
  getTimeline: (id: string, signal?: AbortSignal) =>
    request<TimelineEvent[]>(`/api/incidents/${enc(id)}/timeline`, { signal }),
  /** A 404 here means "you have not investigated this yet". CloudFront answers every 404, the API's included, with
   *  the site's HTML 404 page, so the error code in the body cannot be relied on: go by the status. */
  latestInvestigation: async (id: string, signal?: AbortSignal) => {
    try {
      return await request<Investigation>(`/api/incidents/${enc(id)}/investigations/latest`, { signal });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        throw new ApiError("No investigation has been run for this incident.", 404, "investigation_not_found");
      }
      throw e;
    }
  },
  getInvestigation: (id: string, signal?: AbortSignal) =>
    request<Investigation>(`/api/investigations/${enc(id)}`, { signal }),
  startInvestigation: (incidentId: string) =>
    request<Investigation>(`/api/incidents/${enc(incidentId)}/investigations`, { method: "POST" }),
  approve: (investigationId: string) =>
    request<Investigation>(`/api/investigations/${enc(investigationId)}/remediation/approve`, { method: "POST" }),
  feedback: (investigationId: string, helpful: boolean, comment: string) =>
    request<Investigation>(`/api/investigations/${enc(investigationId)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ helpful, comment: comment.trim() || null }),
    }),
  reject: (investigationId: string) =>
    request<Investigation>(`/api/investigations/${enc(investigationId)}/remediation/reject`, { method: "POST" }),
};
