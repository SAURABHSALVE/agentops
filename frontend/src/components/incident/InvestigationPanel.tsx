import type { Investigation } from "@/lib/types";
import type { Busy } from "@/lib/use-workspace";

interface Props {
  investigation: Investigation | null;
  busy: Busy;
  onStart: () => void;
}

function formatArgs(args: Record<string, string | number>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
}

export function InvestigationPanel({ investigation, busy, onStart }: Props) {
  const running = investigation?.status === "running" || busy === "starting";
  const label = running ? "Investigating…" : investigation ? "Re-run investigation" : "Investigate";
  const tokens = investigation ? investigation.usage.input_tokens + investigation.usage.output_tokens : 0;

  return (
    <section aria-labelledby="inv-heading" className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="inv-heading" className="font-semibold">
          Investigation
        </h2>
        <button
          type="button"
          onClick={onStart}
          disabled={running || busy !== null}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          {label}
        </button>
      </div>

      <p role="status" className="mt-2 text-sm text-muted">
        {!investigation && !running && "No investigation has been run for this incident."}
        {running && "The agent is calling read-only diagnostic tools…"}
        {investigation?.status === "completed" && `Finished after ${investigation.tool_calls.length} tool calls.`}
        {investigation?.status === "failed" && "Investigation stopped before findings were produced."}
      </p>

      {investigation && (
        <p className="mt-1 text-xs text-muted">
          Provider: <span className="font-medium text-foreground">{investigation.provider.label}</span>
          {tokens > 0 &&
            ` · ${investigation.usage.rounds} rounds · ${tokens.toLocaleString("en-US")} tokens`}
          {` · ${investigation.id}`}
        </p>
      )}

      {investigation?.status === "failed" && investigation.failure && (
        <p role="alert" className="mt-3 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {investigation.failure.message}
          <span className="ml-1 font-mono text-xs">({investigation.failure.code})</span>
        </p>
      )}

      {investigation && investigation.tool_calls.length > 0 && (
        <ol className="mt-3 divide-y divide-border rounded border border-border" aria-label="Tool calls">
          {investigation.tool_calls.map((call) => (
            <li key={call.id} className="px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs">
                  {call.name}({formatArgs(call.arguments)})
                </span>
                <span className={`text-xs ${call.status === "error" ? "text-danger" : "text-muted"}`}>
                  {call.status === "ok"
                    ? `ok · ${call.duration_ms} ms`
                    : `error: ${call.error_code} · ${call.duration_ms} ms`}
                </span>
              </div>
              <p className="mt-1 text-muted">{call.result_summary}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
