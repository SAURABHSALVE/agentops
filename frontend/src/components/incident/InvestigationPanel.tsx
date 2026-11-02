import { failureHelp, toolSentence } from "@/lib/plain";
import type { Investigation } from "@/lib/types";
import type { AuthState } from "@/lib/use-auth";
import type { Busy } from "@/lib/use-workspace";

interface Props {
  investigation: Investigation | null;
  busy: Busy;
  onStart: () => void;
  auth: AuthState;
  onSignIn: () => void;
}

function formatArgs(args: Record<string, string | number>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
}

export function InvestigationPanel({ investigation, busy, onStart, auth, onSignIn }: Props) {
  const running = investigation?.status === "running" || busy === "starting";
  const label = running ? "Investigating…" : investigation ? "Re-run investigation" : "Investigate";
  const tokens = investigation ? investigation.usage.input_tokens + investigation.usage.output_tokens : 0;

  return (
    <section aria-labelledby="inv-heading" className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="inv-heading" className="font-semibold">
          Investigation
        </h2>
        {auth.status === "signed_out" || auth.status === "unavailable" ? (
          <button
            type="button"
            onClick={onSignIn}
            disabled={auth.status === "unavailable"}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
          >
            Sign in to investigate
          </button>
        ) : (
          <button
            type="button"
            onClick={onStart}
            disabled={running || busy !== null || auth.status === "loading"}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
          >
            {label}
          </button>
        )}
      </div>

      {auth.status === "signed_out" && (
        <div className="mt-2 space-y-1 text-sm text-muted">
          <p>
            Anyone can read the incidents. Sign in with your email to run an investigation: what you run, approve and
            rate is private to your account.
          </p>
          <p>
            New here? Choose <span className="font-medium text-foreground">Sign up</span> on the next page. You will be
            emailed a 6-digit code to confirm your address; if it does not arrive within a minute, check your spam
            folder.
          </p>
        </div>
      )}
      {auth.status === "unavailable" && (
        <p className="mt-2 text-sm text-muted">Sign-in is required here but is not set up on this site.</p>
      )}

      <p role="status" className="mt-2 text-sm text-muted">
        {!investigation && !running && "No investigation has been run for this incident."}
        {running &&
          `The agent is checking logs, metrics and releases (read-only)… ${investigation?.tool_calls.length ?? 0} checks so far.`}
        {investigation?.status === "completed" && `Finished after ${investigation.tool_calls.length} checks.`}
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
        <div role="alert" className="mt-3 rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          <p className="font-medium">{failureHelp(investigation.failure.code).title}</p>
          <p className="mt-1">{failureHelp(investigation.failure.code).next}</p>
          <p className="mt-1 text-xs">
            {investigation.failure.message}
            <span className="ml-1 font-mono">({investigation.failure.code})</span>
          </p>
        </div>
      )}

      {investigation && investigation.tool_calls.length > 0 && (
        <>
        <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">How the agent checked this</h3>
        <ol className="mt-1 divide-y divide-border rounded border border-border" aria-label="Tool calls">
          {investigation.tool_calls.map((call) => (
            <li key={call.id} className="px-3 py-2 text-sm">
              <p className="font-medium">{toolSentence(call)}</p>
              <div className="mt-0.5 flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-muted">
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
        </>
      )}
    </section>
  );
}
