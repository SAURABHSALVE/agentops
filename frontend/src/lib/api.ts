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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const headers: Record<string, string> = { Accept: "application/json" };
    const code = init?.method === "POST" ? getAccessCode() : "";
    if (code) headers["X-Access-Code"] = code;
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
  latestInvestigation: (id: string, signal?: AbortSignal) =>
    request<Investigation>(`/api/incidents/${enc(id)}/investigations/latest`, { signal }),
  getInvestigation: (id: string, signal?: AbortSignal) =>
    request<Investigation>(`/api/investigations/${enc(id)}`, { signal }),
  startInvestigation: (incidentId: string) =>
    request<Investigation>(`/api/incidents/${enc(incidentId)}/investigations`, { method: "POST" }),
  approve: (investigationId: string) =>
    request<Investigation>(`/api/investigations/${enc(investigationId)}/remediation/approve`, { method: "POST" }),
  reject: (investigationId: string) =>
    request<Investigation>(`/api/investigations/${enc(investigationId)}/remediation/reject`, { method: "POST" }),
};
