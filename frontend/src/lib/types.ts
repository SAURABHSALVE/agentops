// Shapes follow the planned FastAPI contract (snake_case fields) so Phase 2/3 can swap
// the mock data for real API responses without a mapping layer.

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
  detail?: string;
}

export interface Incident {
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
  /** Headline signal shown on the detail page, e.g. "5xx rate: 18% (baseline 0.2%)". */
  signals: { label: string; value: string }[];
  /** Events that exist before any investigation. */
  history: TimelineEvent[];
}

export interface ToolCallRecord {
  name: "get_incident" | "search_logs" | "get_service_metrics" | "get_recent_deployments";
  /** Arguments as displayed (already redacted/validated). */
  args: string;
  duration_ms: number;
  result_summary: string;
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

/** One scripted investigation per incident (Phase 1 mock only). */
export interface MockInvestigation {
  tool_calls: ToolCallRecord[];
  findings: Findings;
}
