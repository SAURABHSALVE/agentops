"use client";

import { useEffect, useReducer, useState } from "react";
import { initialState, reducer } from "@/lib/investigation-reducer";
import { formatDateTime, formatWindow } from "@/lib/format";
import type { Incident, MockInvestigation, TimelineEvent } from "@/lib/types";
import { SeverityBadge, StatusBadge } from "../badges";
import { EvidencePanel } from "./EvidencePanel";
import { InvestigationPanel } from "./InvestigationPanel";
import { RemediationPanel } from "./RemediationPanel";
import { Timeline } from "./Timeline";

const STEP_MS = 800;
const ACTION_MS = 1200;
const FAIL_AT_TOOL = 2;

export function IncidentWorkspace({ incident, script }: { incident: Incident; script: MockInvestigation }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [simulateFailure, setSimulateFailure] = useState(false);
  const remediation = script.findings.remediation;
  const actionId = remediation?.action_id ?? "";

  // Drives the scripted mock flow. Every timer is cleared when the stage changes or the page unmounts.
  useEffect(() => {
    const at = () => new Date().toISOString();
    let timer: ReturnType<typeof setTimeout> | undefined;

    if (state.stage === "running") {
      timer = setTimeout(() => {
        if (simulateFailure && state.toolsDone === FAIL_AT_TOOL) {
          const tool = script.tool_calls[state.toolsDone].name;
          dispatch({ type: "fail", at: at(), message: `${tool} timed out after 5000 ms (simulated failure).` });
        } else if (state.toolsDone < script.tool_calls.length) {
          dispatch({ type: "tool_done", at: at(), script });
        } else {
          dispatch({ type: "complete", at: at(), script });
        }
      }, STEP_MS);
    } else if (state.stage === "executing") {
      timer = setTimeout(() => dispatch({ type: "action_done", at: at(), actionId }), ACTION_MS);
    } else if (state.stage === "verifying") {
      timer = setTimeout(() => dispatch({ type: "verified", at: at() }), ACTION_MS);
    }

    return () => clearTimeout(timer);
  }, [state.stage, state.toolsDone, simulateFailure, script, actionId]);

  const status =
    state.stage === "resolved"
      ? "resolved"
      : state.stage === "running" || state.stage === "executing" || state.stage === "verifying"
        ? "investigating"
        : incident.status;

  const events: TimelineEvent[] = [...incident.history, ...state.events];

  return (
    <div className="space-y-4">
      <header className="rounded-lg border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted">{incident.id}</span>
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={status} />
        </div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight">{incident.title}</h1>
        <p className="mt-1 text-sm text-muted">{incident.summary}</p>
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-muted">Service</dt>
            <dd className="font-mono">{incident.service}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Started</dt>
            <dd>{formatDateTime(incident.started_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Investigation window</dt>
            <dd>{formatWindow(incident.started_at, incident.window_end)}</dd>
          </div>
          {incident.signals.map((s) => (
            <div key={s.label}>
              <dt className="text-xs text-muted">{s.label}</dt>
              <dd>{s.value}</dd>
            </div>
          ))}
        </dl>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <InvestigationPanel
            stage={state.stage}
            toolsDone={state.toolsDone}
            error={state.error}
            script={script}
            simulateFailure={simulateFailure}
            onSimulateFailureChange={setSimulateFailure}
            onStart={() => dispatch({ type: "start", at: new Date().toISOString() })}
          />
          <EvidencePanel stage={state.stage} findings={script.findings} />
          <RemediationPanel
            stage={state.stage}
            remediation={remediation}
            onApprove={() => dispatch({ type: "approve", at: new Date().toISOString(), actionId })}
            onReject={() => dispatch({ type: "reject", at: new Date().toISOString(), actionId })}
          />
        </div>
        <Timeline events={events} />
      </div>
    </div>
  );
}
