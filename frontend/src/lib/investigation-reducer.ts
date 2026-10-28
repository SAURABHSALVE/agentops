// Pure state machine for the Phase 1 mock investigation flow.
// In later phases the backend owns these states; the UI will render what the API returns.

import type { MockInvestigation, TimelineEvent } from "./types";

export type Stage =
  | "idle"
  | "running"
  | "failed"
  | "complete"
  | "executing" // approved, simulated action in flight
  | "verifying" // action succeeded; verification running (a separate state from "resolved")
  | "resolved" // verification passed
  | "rejected";

export interface State {
  stage: Stage;
  /** Number of tool calls that have finished (the next one is "running"). */
  toolsDone: number;
  error: string | null;
  /** Events created during this session (appended after the incident's own history). */
  events: TimelineEvent[];
  seq: number;
}

export type Action =
  | { type: "start"; at: string }
  | { type: "tool_done"; at: string; script: MockInvestigation }
  | { type: "complete"; at: string; script: MockInvestigation }
  | { type: "fail"; at: string; message: string }
  | { type: "approve"; at: string; actionId: string }
  | { type: "action_done"; at: string; actionId: string }
  | { type: "verified"; at: string }
  | { type: "reject"; at: string; actionId: string };

export const initialState: State = { stage: "idle", toolsDone: 0, error: null, events: [], seq: 0 };

function push(state: State, e: Omit<TimelineEvent, "id">): Pick<State, "events" | "seq"> {
  const seq = state.seq + 1;
  return { seq, events: [...state.events, { ...e, id: `session-${seq}` }] };
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "start":
      if (state.stage !== "idle" && state.stage !== "failed") return state;
      return {
        ...state,
        stage: "running",
        toolsDone: 0,
        error: null,
        ...push(state, { at: action.at, kind: "agent", title: "Investigation started" }),
      };

    case "tool_done": {
      if (state.stage !== "running") return state;
      const call = action.script.tool_calls[state.toolsDone];
      if (!call) return state;
      return {
        ...state,
        toolsDone: state.toolsDone + 1,
        ...push(state, {
          at: action.at,
          kind: "tool",
          title: `${call.name}(${call.args})`,
          detail: `${call.result_summary} · ${call.duration_ms} ms`,
        }),
      };
    }

    case "complete":
      if (state.stage !== "running") return state;
      return {
        ...state,
        stage: "complete",
        ...push(state, {
          at: action.at,
          kind: "agent",
          title: "Findings ready",
          detail: `Confidence: ${action.script.findings.confidence}`,
        }),
      };

    case "fail":
      if (state.stage !== "running") return state;
      return {
        ...state,
        stage: "failed",
        error: action.message,
        ...push(state, { at: action.at, kind: "error", title: "Investigation failed", detail: action.message }),
      };

    case "approve":
      if (state.stage !== "complete") return state; // duplicate clicks are ignored
      return {
        ...state,
        stage: "executing",
        ...push(state, { at: action.at, kind: "approval", title: "Remediation approved", detail: action.actionId }),
      };

    case "action_done":
      if (state.stage !== "executing") return state;
      return {
        ...state,
        stage: "verifying",
        ...push(state, { at: action.at, kind: "action", title: "Simulated action succeeded", detail: action.actionId }),
      };

    case "verified":
      if (state.stage !== "verifying") return state;
      return {
        ...state,
        stage: "resolved",
        ...push(state, { at: action.at, kind: "verification", title: "Verification passed: simulated incident resolved" }),
      };

    case "reject":
      if (state.stage !== "complete") return state;
      return {
        ...state,
        stage: "rejected",
        ...push(state, {
          at: action.at,
          kind: "approval",
          title: "Remediation rejected",
          detail: `${action.actionId} was not executed.`,
        }),
      };
  }
}
