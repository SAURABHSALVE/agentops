import { failureHelp, toolSentence } from "@/lib/plain";
import type { Investigation } from "@/lib/types";
import type { AuthState } from "@/lib/use-auth";
import type { Busy } from "@/lib/use-workspace";
import { Icon, Note, Spinner, Stepper, type StepInfo } from "../ui";

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

/** Where the person is in "sign in, investigate, read the answer", so they always know the next thing to do. */
function progress(auth: AuthState, investigation: Investigation | null, running: boolean) {
  const steps: StepInfo[] = [];
  if (auth.status !== "off") steps.push({ label: "Sign in", hint: "Free, with your email" });
  steps.push({ label: "Investigate", hint: "Takes under a minute" });
  steps.push({ label: "Read the answer", hint: "Cause, proof, next step" });
  const offset = auth.status === "off" ? 0 : 1; // the sign-in step is absent when the site needs none
  const needsSignIn = auth.status === "signed_out" || auth.status === "unavailable" || auth.status === "loading";
  const finished = investigation?.status === "completed" && !running;
  return { steps, current: needsSignIn ? 0 : finished ? offset + 1 : offset };
}

export function InvestigationPanel({ investigation, busy, onStart, auth, onSignIn }: Props) {
  const running = investigation?.status === "running" || busy === "starting";
  const label = running ? "Investigating…" : investigation ? "Re-run investigation" : "Investigate";
  const tokens = investigation ? investigation.usage.input_tokens + investigation.usage.output_tokens : 0;
  const { steps, current } = progress(auth, investigation, running);
  const calls = investigation?.tool_calls ?? [];

  return (
    <section aria-labelledby="inv-heading" className="card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="inv-heading" className="flex items-center gap-2 text-base font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <Icon name="search" className="h-4 w-4" />
          </span>
          Investigation
        </h2>
        {auth.status === "signed_out" || auth.status === "unavailable" ? (
          <button type="button" onClick={onSignIn} disabled={auth.status === "unavailable"} className="btn btn-primary">
            Sign in to investigate
          </button>
        ) : (
          <button
            type="button"
            onClick={onStart}
            disabled={running || busy !== null || auth.status === "loading"}
            className="btn btn-primary"
          >
            {running && <Spinner />}
            {label}
          </button>
        )}
      </div>

      <div className="mt-4">
        <Stepper steps={steps} current={current} label="Your progress" />
      </div>

      {auth.status === "signed_out" && (
        <div className="mt-3 space-y-1 text-sm text-muted">
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
        <p className="mt-3 text-sm text-muted">Sign-in is required here but is not set up on this site.</p>
      )}

      <p role="status" className="mt-3 text-sm text-muted">
        {!investigation && !running && "No investigation has been run for this incident."}
        {!investigation && !running && auth.status !== "signed_out" && auth.status !== "unavailable" && (
          <span className="block">
            Press <span className="font-medium text-foreground">Investigate</span>. The agent reads the logs, metrics and
            recent releases (read-only) and shows you each check as it goes.
          </span>
        )}
        {running &&
          `The agent is checking logs, metrics and releases (read-only)… ${calls.length} checks so far.`}
        {investigation?.status === "completed" && (
          <>
            {`Finished after ${calls.length} checks. `}
            <a href="#findings" className="link">
              Jump to the answer
            </a>
          </>
        )}
        {investigation?.status === "failed" && "Investigation stopped before findings were produced."}
      </p>

      {running && (
        <ul aria-label="Progress" className="mt-3 space-y-1.5 text-sm">
          {calls.map((c) => (
            <li key={c.id} className="enter flex items-center gap-2">
              <Icon name="check" className={`h-4 w-4 shrink-0 ${c.status === "error" ? "text-danger" : "text-ok"}`} />
              {toolSentence(c)}
            </li>
          ))}
          <li className="flex items-center gap-2 text-muted">
            <span className="live-dot" aria-hidden="true" />
            {calls.length === 0 ? "Starting…" : "Looking at what it found…"}
          </li>
        </ul>
      )}

      {investigation && (
        <p className="mt-2 text-xs text-muted">
          Provider: <span className="font-medium text-foreground">{investigation.provider.label}</span>
          {tokens > 0 && ` · ${investigation.usage.rounds} rounds · ${tokens.toLocaleString("en-US")} tokens`}
          {` · ${investigation.id}`}
        </p>
      )}

      {investigation?.status === "failed" && investigation.failure && (
        <Note tone="danger" alert title={failureHelp(investigation.failure.code).title} className="mt-3">
          <p>{failureHelp(investigation.failure.code).next}</p>
          <p className="mt-1 text-xs">
            {investigation.failure.message}
            <span className="ml-1 font-mono">({investigation.failure.code})</span>
          </p>
        </Note>
      )}

      {calls.length > 0 && !running && (
        <details className="disclosure mt-4" open>
          <summary className="text-sm">
            How the agent checked this <span className="font-normal text-muted">({calls.length} checks)</span>
          </summary>
          <ol className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border" aria-label="Tool calls">
            {calls.map((call) => (
              <li key={call.id} className="px-3.5 py-2.5 text-sm">
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
        </details>
      )}
    </section>
  );
}
