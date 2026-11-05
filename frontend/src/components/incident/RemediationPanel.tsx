import type { Busy } from "@/lib/use-workspace";
import type { Investigation, RemediationState } from "@/lib/types";
import { Icon, Note, Spinner } from "../ui";

interface Props {
  investigation: Investigation | null;
  busy: Busy;
  onApprove: () => void;
  onReject: () => void;
}

function Step({ label, state, tone }: { label: string; state: string; tone?: "ok" | "bad" }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3.5 py-2.5">
      <span className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className={`h-2 w-2 rounded-full ${tone === "bad" ? "bg-danger" : tone === "ok" ? "bg-ok" : "bg-border-strong"}`}
        />
        {label}
      </span>
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
    <div className="enter space-y-2">
      <ol role="status" className="divide-y divide-border overflow-hidden rounded-xl border border-border">
        <Step label="Approval" state="Approved" tone="ok" />
        <Step
          label="Simulated action"
          state={
            state.status === "executing" ? "Running…" : state.status === "action_failed" ? "Failed" : "Succeeded"
          }
          tone={state.status === "action_failed" ? "bad" : state.status === "executing" ? undefined : "ok"}
        />
        <Step label="Verification" state={verification.text} tone={verification.tone} />
      </ol>
      {checks.length > 0 && (
        <ul className="rounded-xl bg-surface-2 px-3.5 py-2.5 font-mono text-xs">
          {checks.map((c) => (
            <li key={c.metric}>
              {c.metric}: {c.observed} {c.passed ? "<" : "≥"} {c.threshold} {c.passed ? "✓" : "✗"}
            </li>
          ))}
        </ul>
      )}
      {state.status === "not_resolved" && (
        <Note tone="danger" alert>
          The action ran, but the metrics did not recover. The incident stays open. Investigate further or escalate;
          re-running the investigation will not change the outcome by itself.
        </Note>
      )}
      {state.status === "action_failed" && (
        <Note tone="danger" alert>
          {state.error ?? "The simulated action could not run."}
        </Note>
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
    <section aria-labelledby="rem-heading" className="card p-5">
      <h2 id="rem-heading" className="flex items-center gap-2 text-base font-semibold">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <Icon name="shield" className="h-4 w-4" />
        </span>
        Remediation approval
      </h2>

      {!done && (
        <p className="mt-3 text-sm text-muted">
          Available once findings are ready. The agent can only suggest a fix; nothing runs unless you approve it.
        </p>
      )}

      {done && !remediation && (
        <p className="mt-3 text-sm text-muted">
          No remediation proposed. The evidence does not support a specific action, so none is offered.
        </p>
      )}

      {done && remediation && state && (
        <div className="mt-4 space-y-3 text-sm">
          <dl className="space-y-3">
            <div>
              <dt className="eyebrow">Proposed action</dt>
              <dd className="mt-1">
                <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{remediation.action_id}</code>
                <span className="mt-1 block">{remediation.description}</span>
              </dd>
            </div>
            <div>
              <dt className="eyebrow">Expected effect</dt>
              <dd className="mt-1">{remediation.expected_effect}</dd>
            </div>
          </dl>
          <p className="rounded-xl bg-surface-2 px-3.5 py-2.5 text-xs text-muted">
            This is a simulation: it runs one pre-approved action and then checks whether the metrics recovered. No real
            infrastructure is changed.
          </p>

          {state.status === "proposed" && (
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={onApprove} disabled={deciding} className="btn btn-primary">
                {busy === "approving" && <Spinner />}
                {busy === "approving" ? "Running…" : "Approve simulated action"}
              </button>
              <button type="button" onClick={onReject} disabled={deciding} className="btn btn-secondary">
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
