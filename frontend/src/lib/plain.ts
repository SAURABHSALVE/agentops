// Plain-English wording for things the API reports as codes. Display only: nothing here changes what the
// backend decides, and an unknown code falls back to a generic sentence instead of throwing.

import type { Confidence, ToolCallRecord } from "./types";

export const CONFIDENCE_MEANING: Record<Confidence, string> = {
  high: "Strong evidence points to one cause. Still worth a quick look before you act.",
  medium: "The evidence fits this cause, but something is unconfirmed. Treat it as the leading theory.",
  low: "Not enough evidence to be sure. Treat this as a lead to check, not an answer.",
};

/** A sentence for one tool call, e.g. `Searched the logs for "timeout"`. */
export function toolSentence(call: Pick<ToolCallRecord, "name" | "arguments">): string {
  const a = call.arguments;
  switch (call.name) {
    case "get_incident":
      return "Read the incident details";
    case "search_logs":
      return a.query ? `Searched the logs for "${String(a.query)}"` : "Searched the logs";
    case "get_service_metrics":
      return a.metric ? `Checked the ${String(a.metric).replace(/_ms$/, "").replaceAll("_", " ")} metric` : "Checked a service metric";
    case "get_recent_deployments":
      return a.hours ? `Looked for releases in the last ${String(a.hours)} hours` : "Looked for recent releases";
    default:
      return "Made a request that was refused";
  }
}

interface FailureHelp {
  title: string;
  next: string;
}

const FAILURE_HELP: Record<string, FailureHelp> = {
  provider_error: {
    title: "The AI service could not be reached or refused the request.",
    next: "This is usually temporary. Wait a minute and run the investigation again.",
  },
  budget_exceeded: {
    title: "The investigation used up its size limit before it could finish.",
    next: "Run it again. If it keeps happening, the incident needs a human to narrow it down.",
  },
  max_rounds_exceeded: {
    title: "The agent kept checking and never reached a conclusion.",
    next: "Run it again. If it repeats, treat the incident as hard to diagnose automatically.",
  },
  invalid_output: {
    title: "The agent produced an answer the system could not accept.",
    next: "Nothing was made up or shown. Run the investigation again.",
  },
  ungrounded_evidence: {
    title: "The agent cited evidence it never actually looked at, so its answer was thrown away.",
    next: "This protects you from an unsupported answer. Run the investigation again.",
  },
  remediation_not_allowed: {
    title: "The agent suggested an action that is not on the approved list, so it was blocked.",
    next: "Nothing ran. Run the investigation again.",
  },
  internal_error: {
    title: "Something went wrong on our side, or the server restarted mid-run.",
    next: "Nothing was changed. Run the investigation again.",
  },
};

export function failureHelp(code: string): FailureHelp {
  return (
    FAILURE_HELP[code] ?? {
      title: "The investigation stopped before it produced an answer.",
      next: "Nothing was changed. Run the investigation again.",
    }
  );
}

const ERROR_HELP: Record<string, string> = {
  network_error: "We could not reach the server. Check your connection and try again.",
  rate_limited: "Too many investigations in a short time. Wait a minute and try again.",
  unauthorized: "That action needs the access code. Enter it above and try again.",
  sign_in_required: "Sign in to run investigations, approve actions and leave feedback.",
  invalid_token: "Your sign-in has expired. Sign in again to continue.",
  auth_unavailable: "Sign-in is temporarily unavailable. Try again in a minute.",
  investigation_in_progress: "An investigation is already running for this incident. It will update here when done.",
};

/** Turn an API error into advice a non-engineer can act on. Falls back to the server's own message. */
export function friendlyError(code: string, fallback: string): string {
  return ERROR_HELP[code] ?? fallback;
}

/** One line per demo incident: what you will see. Keys must be ids the backend serves. */
export const DEMO_HINTS: Record<string, { tag: string; hint: string }> = {
  "INC-1001": { tag: "Start here", hint: "A clear cause. Approve the fix and watch it get verified." },
  "INC-1002": { tag: "Clear cause", hint: "A slow query after a release. Medium confidence." },
  "INC-1003": { tag: "Clear cause", hint: "A queue backing up because something downstream is failing." },
  "INC-1004": { tag: "Ambiguous", hint: "Not enough evidence. The agent should say so and offer no fix." },
  "INC-1005": { tag: "Safety test", hint: "A log line tries to give the agent orders. It must be ignored." },
  "INC-1006": { tag: "Fix fails", hint: "The action runs but the problem stays. The app must not say it is fixed." },
  "INC-2001": { tag: "Start here", hint: "Measured from a real running lab: payments calls timing out." },
  "INC-2002": { tag: "Real data", hint: "A real slow database query after a release." },
  "INC-2003": { tag: "Fix fails", hint: "Restarting workers does not help; the verification fails for real." },
};
