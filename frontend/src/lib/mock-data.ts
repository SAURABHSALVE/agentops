// PHASE 1 MOCK DATA. Entirely fictional, hard-coded in the browser bundle.
// It is replaced by the deterministic FastAPI simulator in Phase 2 and by real
// tool results in Phase 3. None of it is real telemetry.

import type { Incident, MockInvestigation } from "./types";

export const incidents: Incident[] = [
  {
    id: "INC-1001",
    title: "Elevated HTTP 5xx on checkout",
    service: "checkout-api",
    severity: "critical",
    status: "open",
    summary: "Checkout requests are failing with 502/504 responses; order completion rate has dropped.",
    started_at: "2026-10-28T09:12:00Z",
    window_end: "2026-10-28T09:42:00Z",
    updated_at: "2026-10-28T09:42:00Z",
    signals: [
      { label: "5xx rate", value: "18.4% (baseline 0.2%)" },
      { label: "p99 latency", value: "3,120 ms (baseline 240 ms)" },
    ],
    history: [
      {
        id: "e-1001-1",
        at: "2026-10-28T09:12:00Z",
        kind: "incident",
        title: "Alert fired: 5xx rate above 5% for 5 min",
      },
      {
        id: "e-1001-2",
        at: "2026-10-28T09:20:00Z",
        kind: "incident",
        title: "Severity raised to critical",
        detail: "Order completion rate dropped below threshold.",
      },
    ],
  },
  {
    id: "INC-1002",
    title: "Catalog latency elevated",
    service: "catalog-api",
    severity: "high",
    status: "investigating",
    summary: "Product listing endpoints are slow; p95 latency has risen steadily since mid-morning.",
    started_at: "2026-10-28T08:40:00Z",
    window_end: "2026-10-28T09:40:00Z",
    updated_at: "2026-10-28T09:38:00Z",
    signals: [
      { label: "p95 latency", value: "2,450 ms (baseline 180 ms)" },
      { label: "Error rate", value: "0.4% (baseline 0.3%)" },
    ],
    history: [
      {
        id: "e-1002-1",
        at: "2026-10-28T08:40:00Z",
        kind: "incident",
        title: "Alert fired: p95 latency above 1,000 ms",
      },
    ],
  },
  {
    id: "INC-1003",
    title: "Worker queue backlog growing",
    service: "worker-service",
    severity: "high",
    status: "open",
    summary: "Messages are accumulating faster than they are processed; oldest message age is increasing.",
    started_at: "2026-10-28T09:05:00Z",
    window_end: "2026-10-28T09:45:00Z",
    updated_at: "2026-10-28T09:44:00Z",
    signals: [
      { label: "Queue depth", value: "14,200 (baseline < 200)" },
      { label: "Oldest message age", value: "19 min" },
    ],
    history: [
      {
        id: "e-1003-1",
        at: "2026-10-28T09:05:00Z",
        kind: "incident",
        title: "Alert fired: queue depth above 5,000",
      },
    ],
  },
  {
    id: "INC-1004",
    title: "Intermittent search latency spikes",
    service: "search-api",
    severity: "medium",
    status: "open",
    summary: "Short, irregular latency spikes reported by a few users; no sustained error increase.",
    started_at: "2026-10-28T08:55:00Z",
    window_end: "2026-10-28T09:35:00Z",
    updated_at: "2026-10-28T09:35:00Z",
    signals: [
      { label: "p99 latency", value: "Spikes to 1,400 ms (baseline 300 ms)" },
      { label: "Error rate", value: "Within normal range" },
    ],
    history: [
      {
        id: "e-1004-1",
        at: "2026-10-28T08:55:00Z",
        kind: "incident",
        title: "Reported by support: intermittent slow searches",
      },
    ],
  },
];

export const mockInvestigations: Record<string, MockInvestigation> = {
  "INC-1001": {
    tool_calls: [
      { name: "get_incident", args: 'incident_id="INC-1001"', duration_ms: 12, result_summary: "Incident record, 30-minute window" },
      { name: "search_logs", args: 'service="checkout-api", query="timeout", limit=50', duration_ms: 41, result_summary: "37 matching entries, mostly upstream timeouts" },
      { name: "get_service_metrics", args: 'service="checkout-api", metric="error_rate"', duration_ms: 28, result_summary: "Error rate rises sharply at 09:12" },
      { name: "get_recent_deployments", args: 'service="checkout-api", hours=6', duration_ms: 15, result_summary: "No deployments in the last 6 hours" },
    ],
    findings: {
      summary: "Checkout 5xx errors began at 09:12 and coincide with repeated timeouts calling the payments dependency. No deployment preceded the onset.",
      root_cause_hypothesis: "Calls from checkout-api to the payments dependency are timing out, causing 502/504 responses.",
      confidence: "high",
      confidence_rationale: "Timeout errors dominate the incident window, their onset matches the metric change, and the deployment history rules out a recent code change.",
      evidence: [
        { source: "logs", reference_id: "log-104", observation: "Upstream request to payments dependency timed out after 3,000 ms." },
        { source: "logs", reference_id: "log-121", observation: "Same timeout repeated 36 more times between 09:12 and 09:41." },
        { source: "metrics", reference_id: "metric:error_rate:09:12-09:42", observation: "Error rate rose from 0.2% to 18.4% at 09:12." },
        { source: "deployments", reference_id: "deploys:checkout-api:6h", observation: "No deployments recorded in the 6 hours before onset." },
      ],
      alternative_hypotheses: ["Resource exhaustion in checkout-api itself (no supporting log or metric evidence found)."],
      recommended_next_step: "Confirm the payments dependency health check is failing before and after remediation.",
      remediation: {
        action_id: "simulate_clear_dependency_fault",
        description: "Clear the injected timeout fault on the simulated payments dependency.",
        expected_effect: "Dependency latency returns to baseline and checkout 5xx rate falls below 1%.",
        requires_approval: true,
      },
      limitations: ["Based on simulated demo telemetry only.", "Cannot see the dependency's own internal logs."],
    },
  },
  "INC-1002": {
    tool_calls: [
      { name: "get_incident", args: 'incident_id="INC-1002"', duration_ms: 11, result_summary: "Incident record, 60-minute window" },
      { name: "search_logs", args: 'service="catalog-api", query="slow query", limit=50', duration_ms: 47, result_summary: "12 slow-query entries on one query shape" },
      { name: "get_service_metrics", args: 'service="catalog-api", metric="db_query_p95"', duration_ms: 31, result_summary: "Query p95 climbs from 08:40 onward" },
      { name: "get_recent_deployments", args: 'service="catalog-api", hours=6', duration_ms: 17, result_summary: "1 deployment at 08:15" },
    ],
    findings: {
      summary: "Catalog latency is driven by a slow database query that began shortly after a deployment.",
      root_cause_hypothesis: "A product-listing query introduced in the 08:15 deployment is running without a supporting index, saturating the database.",
      confidence: "medium",
      confidence_rationale: "Slow-query logs and the deployment timing agree, but no query plan was available to confirm the missing index.",
      evidence: [
        { source: "logs", reference_id: "log-212", observation: "products_by_category query took 4,800 ms." },
        { source: "metrics", reference_id: "metric:db_query_p95:08:40-09:40", observation: "Database query p95 rose from 35 ms to 2,100 ms." },
        { source: "deployments", reference_id: "deploy-419", observation: "Deployment at 08:15 changed product-listing queries, 25 minutes before onset." },
      ],
      alternative_hypotheses: ["Unrelated database load from a batch job (no evidence found in available tools)."],
      recommended_next_step: "Inspect the query plan for products_by_category to confirm a sequential scan.",
      remediation: {
        action_id: "simulate_kill_slow_query",
        description: "Terminate the long-running simulated query and reset the connection pool.",
        expected_effect: "Query p95 returns toward baseline; the underlying index fix still needs follow-up.",
        requires_approval: true,
      },
      limitations: ["Based on simulated demo telemetry only.", "Remediation is a mitigation, not a permanent fix."],
    },
  },
  "INC-1003": {
    tool_calls: [
      { name: "get_incident", args: 'incident_id="INC-1003"', duration_ms: 10, result_summary: "Incident record, 40-minute window" },
      { name: "search_logs", args: 'service="worker-service", query="error", limit=50', duration_ms: 39, result_summary: "212 downstream 503 errors, retries visible" },
      { name: "get_service_metrics", args: 'service="worker-service", metric="queue_depth"', duration_ms: 26, result_summary: "Queue depth grows linearly from 09:05" },
      { name: "get_recent_deployments", args: 'service="worker-service", hours=6', duration_ms: 14, result_summary: "No deployments in the last 6 hours" },
    ],
    findings: {
      summary: "The worker is retrying messages against a failing downstream notifier, which has stalled consumption and let the queue grow.",
      root_cause_hypothesis: "The downstream notification service is returning 503, causing worker retries that block queue throughput.",
      confidence: "high",
      confidence_rationale: "Every sampled failure is a downstream 503, queue growth starts at the first failure, and no deployment explains it.",
      evidence: [
        { source: "logs", reference_id: "log-308", observation: "Downstream notifier returned 503; retry 3 of 5 scheduled." },
        { source: "metrics", reference_id: "metric:queue_depth:09:05-09:45", observation: "Queue depth rose from under 200 to 14,200." },
        { source: "deployments", reference_id: "deploys:worker-service:6h", observation: "No deployments recorded in the 6 hours before onset." },
      ],
      alternative_hypotheses: ["Producer-side traffic surge (inbound rate is flat in the available metrics)."],
      recommended_next_step: "Check downstream notifier status after the worker is reset.",
      remediation: {
        action_id: "simulate_restart_worker",
        description: "Reset the simulated worker state and drain stuck retries.",
        expected_effect: "Queue depth begins to fall once the simulated downstream recovers.",
        requires_approval: true,
      },
      limitations: ["Based on simulated demo telemetry only.", "Restarting does not fix the downstream service."],
    },
  },
  "INC-1004": {
    tool_calls: [
      { name: "get_incident", args: 'incident_id="INC-1004"', duration_ms: 12, result_summary: "Incident record, 40-minute window" },
      { name: "search_logs", args: 'service="search-api", query="slow", limit=50', duration_ms: 36, result_summary: "6 unrelated warnings, no common pattern" },
      { name: "get_service_metrics", args: 'service="search-api", metric="p99_latency_ms"', duration_ms: 24, result_summary: "Three isolated spikes, no sustained trend" },
      { name: "get_recent_deployments", args: 'service="search-api", hours=6', duration_ms: 16, result_summary: "1 documentation-only deployment" },
    ],
    findings: {
      summary: "Latency spikes are real but the available evidence does not point to a single cause.",
      root_cause_hypothesis: "Unknown. Evidence is insufficient to name a root cause.",
      confidence: "low",
      confidence_rationale: "Logs show unrelated warnings, spikes are isolated rather than trending, and the only deployment changed documentation.",
      evidence: [
        { source: "logs", reference_id: "log-402", observation: "Cache warm-up warning appears once, not correlated with spike times." },
        { source: "metrics", reference_id: "metric:p99_latency_ms:08:55-09:35", observation: "Three spikes of under 2 minutes each with no common trigger." },
        { source: "deployments", reference_id: "deploy-433", observation: "Documentation-only change; no runtime impact expected." },
      ],
      alternative_hypotheses: [
        "Garbage-collection pauses (no GC metrics available).",
        "Noisy neighbour on shared infrastructure (no host-level data available).",
      ],
      recommended_next_step: "Collect per-instance latency and GC metrics for the next 30 minutes, then re-run the investigation.",
      remediation: null,
      limitations: [
        "Based on simulated demo telemetry only.",
        "No remediation is proposed because no cause is supported by evidence.",
      ],
    },
  },
};

export function getIncident(id: string): Incident | undefined {
  return incidents.find((i) => i.id === id);
}
