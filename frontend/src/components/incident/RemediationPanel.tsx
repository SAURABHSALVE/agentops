import type { Stage } from "@/lib/investigation-reducer";
import type { Remediation } from "@/lib/types";

interface Props {
  stage: Stage;
  remediation: Remediation | null;
  onApprove: () => void;
  onReject: () => void;
}

function Step({ label, state }: { label: string; state: string }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2">
      <span>{label}</span>
      <span className="text-xs text-muted">{state}</span>
    </li>
  );
}

export function RemediationPanel({ stage, remediation, onApprove, onReject }: Props) {
  const findingsReady = stage !== "idle" && stage !== "running" && stage !== "failed";
  const actioned = stage === "executing" || stage === "verifying" || stage === "resolved";

  return (
    <section aria-labelledby="rem-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="rem-heading" className="font-semibold">
        Remediation approval
      </h2>

      {!findingsReady && (
        <p className="mt-2 text-sm text-muted">Available once findings are ready. Nothing runs without your approval.</p>
      )}

      {findingsReady && !remediation && (
        <p className="mt-2 text-sm text-muted">
          No remediation proposed. The evidence does not support a specific action, so none is offered.
        </p>
      )}

      {findingsReady && remediation && (
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
            Simulated action: runs only an allowlisted simulation. No real infrastructure is changed.
          </p>

          {stage === "complete" && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onApprove}
                className="rounded bg-accent px-3 py-1.5 font-medium text-accent-fg"
              >
                Approve simulated action
              </button>
              <button
                type="button"
                onClick={onReject}
                className="rounded border border-border bg-surface-2 px-3 py-1.5 font-medium hover:border-accent"
              >
                Reject
              </button>
            </div>
          )}

          {stage === "rejected" && (
            <p role="status" className="font-medium">
              Rejected. The action was not executed and nothing changed.
            </p>
          )}

          {actioned && (
            <ol role="status" className="divide-y divide-border rounded border border-border">
              <Step label="Approval" state="Approved" />
              <Step label="Simulated action" state={stage === "executing" ? "Running…" : "Succeeded"} />
              <Step
                label="Verification"
                state={stage === "executing" ? "Pending" : stage === "verifying" ? "Checking…" : "Passed: incident resolved"}
              />
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
