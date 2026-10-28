import type { Stage } from "@/lib/investigation-reducer";
import type { Findings } from "@/lib/types";

const sourceLabel = { logs: "Logs", metrics: "Metrics", deployments: "Deployments" } as const;

export function EvidencePanel({ stage, findings }: { stage: Stage; findings: Findings }) {
  const ready = stage !== "idle" && stage !== "running" && stage !== "failed";

  return (
    <section aria-labelledby="ev-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="ev-heading" className="font-semibold">
        Findings and evidence
      </h2>

      {!ready ? (
        <p className="mt-2 text-sm text-muted">
          {stage === "running" && "Waiting for the investigation to finish…"}
          {stage === "failed" && "No findings: the investigation failed. Retry it above."}
          {stage === "idle" && "No findings yet. Run an investigation to see evidence-backed results."}
        </p>
      ) : (
        <div className="mt-3 space-y-4 text-sm">
          <p>{findings.summary}</p>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Likely root cause</h3>
            <p className="mt-1 font-medium">{findings.root_cause_hypothesis}</p>
            <p className="mt-1 text-muted">
              <span className="font-medium text-foreground capitalize">Confidence: {findings.confidence}.</span>{" "}
              {findings.confidence_rationale}
            </p>
          </div>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Evidence</h3>
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
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Alternatives and unknowns</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {findings.alternative_hypotheses.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Recommended next step</h3>
            <p className="mt-1">{findings.recommended_next_step}</p>
          </div>

          {findings.limitations.length > 0 && (
            <div>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Limitations</h3>
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
