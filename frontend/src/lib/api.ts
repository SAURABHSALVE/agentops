import { clearSession, getSession } from "./auth";
import type {
  AwsConnection,
  AwsConnectionSettings,
  Incident,
  IncidentSummary,
  Investigation,
  InvestigationSummary,
  TimelineEvent,
} from "./types";

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
  /** Absent on an API older than Phase 16. Whether this site offers "connect your AWS account". */
  aws_connect?: { available: boolean };
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
    const method = init?.method;
    const code = method === "POST" || method === "PUT" || method === "DELETE" ? getAccessCode() : "";
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
  /** `null` when you have not investigated this incident yet (a normal answer, not an error). */
  latestInvestigation: (id: string, signal?: AbortSignal) =>
    request<Investigation | null>(`/api/incidents/${enc(id)}/investigations/latest`, { signal }),
  myInvestigations: (signal?: AbortSignal) => request<InvestigationSummary[]>("/api/me/investigations", { signal }),
  deleteMyInvestigations: () => request<{ deleted: number }>("/api/me/investigations", { method: "DELETE" }),
  awsConnection: (signal?: AbortSignal) => request<AwsConnection>("/api/me/aws-connection", { signal }),
  saveAwsConnection: (settings: AwsConnectionSettings) =>
    request<AwsConnection>("/api/me/aws-connection", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...settings, accept_data_use: true }),
    }),
  /** The template as text (the file the user uploads to CloudFormation). */
  awsConnectionTemplate: async (): Promise<string> =>
    JSON.stringify(await request<unknown>("/api/me/aws-connection/template"), null, 2),
  registerAwsRole: (roleArn: string) =>
    request<AwsConnection>("/api/me/aws-connection/role", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role_arn: roleArn.trim() }),
    }),
  disconnectAws: () => request<AwsConnection>("/api/me/aws-connection", { method: "DELETE" }),
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
