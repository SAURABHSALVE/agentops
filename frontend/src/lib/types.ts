// Shapes mirror the FastAPI contract (backend/app/schemas.py and backend/app/agent/models.py).
// Field names are snake_case on purpose so API responses are used as-is, with no mapping layer.

export type Severity = "critical" | "high" | "medium" | "low";
export type IncidentStatus = "open" | "investigating" | "mitigated" | "resolved";
export type Confidence = "low" | "medium" | "high";
export type EvidenceSource = "logs" | "metrics" | "deployments";

export interface TimelineEvent {
  id: string;
  /** ISO-8601 UTC timestamp. */
  at: string;
  kind: "incident" | "agent" | "tool" | "approval" | "action" | "verification" | "error";
  title: string;
  detail?: string | null;
}

export interface IncidentSummary {
  id: string;
  title: string;
  service: string;
  severity: Severity;
  status: IncidentStatus;
  summary: string;
  /** ISO-8601 UTC. */
  started_at: string;
  window_end: string;
  updated_at: string;
}

export interface Incident extends IncidentSummary {
  signals: { label: string; value: string }[];
  history: TimelineEvent[];
}

export interface ToolCallRecord {
  id: string;
  name: string;
  /** Validated arguments only. */
  arguments: Record<string, string | number>;
  status: "ok" | "error";
  error_code: string | null;
  duration_ms: number;
  result_summary: string;
  reference_ids: string[];
  at: string;
}

export interface EvidenceItem {
  source: EvidenceSource;
  reference_id: string;
  observation: string;
}

export interface Remediation {
  action_id: string;
  description: string;
  expected_effect: string;
  requires_approval: true;
}

export interface Findings {
  summary: string;
  root_cause_hypothesis: string;
  confidence: Confidence;
  confidence_rationale: string;
  evidence: EvidenceItem[];
  alternative_hypotheses: string[];
  recommended_next_step: string;
  remediation: Remediation | null;
  limitations: string[];
}

export type InvestigationStatus = "running" | "completed" | "failed";

export interface Failure {
  code: string;
  message: string;
}

export type RemediationStatus =
  | "proposed"
  | "rejected"
  | "executing"
  | "action_failed"
  | "resolved"
  | "not_resolved"
  | "not_verifiable";

export interface VerificationCheck {
  metric: string;
  observed: number;
  threshold: number;
  comparison: "lt";
  passed: boolean;
}

export interface RemediationState {
  action_id: string;
  status: RemediationStatus;
  decided_at: string | null;
  executed_at: string | null;
  verified_at: string | null;
  action: { status: "executed" | "already_applied"; summary: string; simulated: true } | null;
  verification: { status: "resolved" | "not_resolved" | "not_verifiable"; checks: VerificationCheck[] } | null;
  error: string | null;
}

export interface Feedback {
  helpful: boolean;
  comment: string | null;
  at: string;
}

export interface Investigation {
  id: string;
  incident_id: string;
  status: InvestigationStatus;
  provider: { kind: "mock" | "openai"; label: string };
  started_at: string;
  finished_at: string | null;
  tool_calls: ToolCallRecord[];
  findings: Findings | null;
  failure: Failure | null;
  remediation_state: RemediationState | null;
  usage: { rounds: number; tool_calls: number; input_tokens: number; output_tokens: number };
  feedback: Feedback | null;
  simulated: true;
}

/** One row of "my investigations": the newest run of an incident the signed-in user has investigated. */
export interface InvestigationSummary {
  id: string;
  incident_id: string;
  incident_title: string;
  status: InvestigationStatus;
  started_at: string;
  confidence: Confidence | null;
  remediation_status: RemediationStatus | null;
  helpful: boolean | null;
}
