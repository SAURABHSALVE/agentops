import type { Stage } from "@/lib/investigation-reducer";
import type { MockInvestigation } from "@/lib/types";

interface Props {
  stage: Stage;
  toolsDone: number;
  error: string | null;
  script: MockInvestigation;
  simulateFailure: boolean;
  onSimulateFailureChange: (value: boolean) => void;
  onStart: () => void;
}

export function InvestigationPanel({
  stage,
  toolsDone,
  error,
  script,
  simulateFailure,
  onSimulateFailureChange,
  onStart,
}: Props) {
  const running = stage === "running";
  const started = stage !== "idle";
  const canStart = stage === "idle" || stage === "failed";
  const visible = script.tool_calls.slice(0, Math.min(toolsDone + 1, script.tool_calls.length));

  return (
    <section aria-labelledby="inv-heading" className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="inv-heading" className="font-semibold">
          Investigation
        </h2>
        <button
          type="button"
          onClick={onStart}
          disabled={!canStart}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "Investigating…" : stage === "failed" ? "Retry investigation" : started ? "Investigation complete" : "Investigate"}
        </button>
      </div>

      <p role="status" className="mt-2 text-sm text-muted">
        {stage === "idle" && "No investigation has been run for this incident."}
        {running && "Agent is calling diagnostic tools (mock run)…"}
        {stage === "failed" && "Investigation stopped before findings were produced."}
        {(stage === "complete" || stage === "executing" || stage === "verifying" || stage === "resolved" || stage === "rejected") &&
          `Finished after ${script.tool_calls.length} tool calls.`}
      </p>

      {stage === "failed" && error && (
        <p role="alert" className="mt-3 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {started && (
        <ol className="mt-3 divide-y divide-border rounded border border-border" aria-label="Tool calls">
          {visible.map((call, idx) => {
            const done = idx < toolsDone;
            const failedHere = stage === "failed" && idx === toolsDone;
            return (
              <li key={`${call.name}-${idx}`} className="px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs">
                    {call.name}({call.args})
                  </span>
                  <span className="text-xs text-muted">
                    {done ? `ok · ${call.duration_ms} ms` : failedHere ? "error" : "running…"}
                  </span>
                </div>
                {done && <p className="mt-1 text-muted">{call.result_summary}</p>}
              </li>
            );
          })}
        </ol>
      )}

      <details className="mt-4 text-xs text-muted">
        <summary className="cursor-pointer">Mock controls (Phase 1 only)</summary>
        <label className="mt-2 flex items-center gap-2">
          <input
            type="checkbox"
            checked={simulateFailure}
            disabled={running}
            onChange={(e) => onSimulateFailureChange(e.target.checked)}
          />
          Simulate a tool timeout on the third call, to preview the error state
        </label>
      </details>
    </section>
  );
}
