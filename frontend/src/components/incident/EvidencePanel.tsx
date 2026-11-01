import { CONFIDENCE_MEANING, failureHelp } from "@/lib/plain";
import type { Confidence, Investigation } from "@/lib/types";

const sourceLabel = { logs: "Logs", metrics: "Metrics", deployments: "Deployments" } as const;

const confidenceStyle: Record<Confidence, string> = {
  high: "border-ok/40 bg-ok/10 text-ok",
  medium: "border-warn/40 bg-warn/10 text-warn",
  low: "border-danger/40 bg-danger/10 text-danger",
};

export function EvidencePanel({ investigation }: { investigation: Investigation | null }) {
  const findings = investigation?.findings ?? null;
  const status = investigation?.status ?? "none";
  const failure = investigation?.status === "failed" ? failureHelp(investigation.failure?.code ?? "") : null;

  return (
    <section aria-labelledby="ev-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="ev-heading" className="font-semibold">
        Findings and evidence
      </h2>

      {!findings ? (
        <p className="mt-2 text-sm text-muted">
          {status === "running" && "Waiting for the investigation to finish…"}
          {failure && `No findings: ${failure.title} ${failure.next}`}
          {status === "none" && "No findings yet. Run an investigation to see evidence-backed results."}
        </p>
      ) : (
        <div className="mt-3 space-y-4 text-sm">
          <div className="rounded-lg border border-border bg-surface-2 p-4" aria-label="Short answer">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Most likely cause</h3>
            <p className="mt-1 text-base font-semibold">{findings.root_cause_hypothesis}</p>
            <p className="mt-2 flex flex-wrap items-center gap-2">
              <span
                className={`rounded border px-2 py-0.5 text-xs font-medium capitalize ${confidenceStyle[findings.confidence]}`}
              >
                Confidence: {findings.confidence}.
              </span>
              <span className="text-muted">{CONFIDENCE_MEANING[findings.confidence]}</span>
            </p>
            <p className="mt-2 text-muted">{findings.confidence_rationale}</p>
            <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">What to do next</h3>
            <p className="mt-1">{findings.recommended_next_step}</p>
          </div>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">What the agent found</h3>
            <p className="mt-1">{findings.summary}</p>
          </div>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
              Evidence ({findings.evidence.length})
            </h3>
            <p className="mt-1 text-xs text-muted">
              Each item points at a log line, metric or release the agent actually looked at in this run.
            </p>
            <ul className="mt-1 divide-y divide-border rounded border border-border">
              {findings.evidence.map((e) => (
                <li key={e.reference_id} className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs">{sourceLabel[e.source]}</span>
                    <code className="font-mono text-xs">{e.reference_id}</code>
                  </div>
                  <p className="mt-1">{e.observation}</p>
                </li>
              ))}
            </ul>
          </div>

          {findings.alternative_hypotheses.length > 0 && (
            <div>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Other possibilities and unknowns</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {findings.alternative_hypotheses.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          )}

          {findings.limitations.length > 0 && (
            <div>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted">What this answer cannot tell you</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
                {findings.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
