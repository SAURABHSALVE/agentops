import type { Busy } from "@/lib/use-workspace";
import type { Investigation, RemediationState } from "@/lib/types";

interface Props {
  investigation: Investigation | null;
  busy: Busy;
  onApprove: () => void;
  onReject: () => void;
}

function Step({ label, state, tone }: { label: string; state: string; tone?: "ok" | "bad" }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2">
      <span>{label}</span>
      <span className={`text-xs ${tone === "bad" ? "font-medium text-danger" : tone === "ok" ? "text-ok" : "text-muted"}`}>
        {state}
      </span>
    </li>
  );
}

function Outcome({ state }: { state: RemediationState }) {
  const checks = state.verification?.checks ?? [];
  const verification =
    state.status === "executing"
      ? { text: "Pending", tone: undefined }
      : state.status === "resolved"
        ? { text: "Passed: incident resolved", tone: "ok" as const }
        : state.status === "not_resolved"
          ? { text: "FAILED: incident NOT resolved", tone: "bad" as const }
          : state.status === "not_verifiable"
            ? { text: "Not available for this incident", tone: undefined }
            : { text: "Not run", tone: undefined };
  return (
    <div className="space-y-2">
      <ol role="status" className="divide-y divide-border rounded border border-border">
        <Step label="Approval" state="Approved" tone="ok" />
        <Step
          label="Simulated action"
          state={
            state.status === "executing"
              ? "Running…"
              : state.status === "action_failed"
                ? "Failed"
                : "Succeeded"
          }
          tone={state.status === "action_failed" ? "bad" : state.status === "executing" ? undefined : "ok"}
        />
        <Step label="Verification" state={verification.text} tone={verification.tone} />
      </ol>
      {checks.length > 0 && (
        <ul className="rounded bg-surface-2 px-3 py-2 font-mono text-xs">
          {checks.map((c) => (
            <li key={c.metric}>
              {c.metric}: {c.observed} {c.passed ? "<" : "≥"} {c.threshold} {c.passed ? "✓" : "✗"}
            </li>
          ))}
        </ul>
      )}
      {state.status === "not_resolved" && (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          The action ran, but the metrics did not recover. The incident stays open. Investigate further or escalate;
          re-running the investigation will not change the outcome by itself.
        </p>
      )}
      {state.status === "action_failed" && (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {state.error ?? "The simulated action could not run."}
        </p>
      )}
    </div>
  );
}

export function RemediationPanel({ investigation, busy, onApprove, onReject }: Props) {
  const done = investigation?.status === "completed";
  const remediation = investigation?.findings?.remediation ?? null;
  const state = investigation?.remediation_state ?? null;
  const deciding = busy === "approving" || busy === "rejecting";

  return (
    <section aria-labelledby="rem-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="rem-heading" className="font-semibold">
        Remediation approval
      </h2>

      {!done && (
        <p className="mt-2 text-sm text-muted">
          Available once findings are ready. The agent can only suggest a fix; nothing runs unless you approve it.
        </p>
      )}

      {done && !remediation && (
        <p className="mt-2 text-sm text-muted">
          No remediation proposed. The evidence does not support a specific action, so none is offered.
        </p>
      )}

      {done && remediation && state && (
        <div className="mt-3 space-y-3 text-sm">
          <dl className="space-y-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted">Proposed action</dt>
              <dd>
                <code className="font-mono text-xs">{remediation.action_id}</code>
                <span className="block">{remediation.description}</span>
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted">Expected effect</dt>
              <dd>{remediation.expected_effect}</dd>
            </div>
          </dl>
          <p className="rounded bg-surface-2 px-3 py-2 text-xs text-muted">
            This is a simulation: it runs one pre-approved action and then checks whether the metrics recovered. No real
            infrastructure is changed.
          </p>

          {state.status === "proposed" && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onApprove}
                disabled={deciding}
                className="rounded bg-accent px-3 py-1.5 font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "approving" ? "Running…" : "Approve simulated action"}
              </button>
              <button
                type="button"
                onClick={onReject}
                disabled={deciding}
                className="rounded border border-border bg-surface-2 px-3 py-1.5 font-medium hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          )}

          {state.status === "rejected" && (
            <p role="status" className="font-medium">
              Rejected. The action was not executed and nothing changed.
            </p>
          )}

          {state.status !== "proposed" && state.status !== "rejected" && <Outcome state={state} />}
        </div>
      )}
    </section>
  );
}
