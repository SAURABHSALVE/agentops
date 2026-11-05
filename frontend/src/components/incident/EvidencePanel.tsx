import { CONFIDENCE_MEANING, failureHelp } from "@/lib/plain";
import type { Confidence, Investigation } from "@/lib/types";
import { ConfidenceMeter, Icon } from "../ui";

const sourceLabel = { logs: "Logs", metrics: "Metrics", deployments: "Deployments" } as const;

const confidenceStyle: Record<Confidence, string> = {
  high: "border-ok/40 bg-ok/10 text-ok",
  medium: "border-warn/40 bg-warn/10 text-warn",
  low: "border-danger/40 bg-danger/10 text-danger",
};

function Heading({ children }: { children: React.ReactNode }) {
  return <h3 className="eyebrow">{children}</h3>;
}

export function EvidencePanel({ investigation }: { investigation: Investigation | null }) {
  const findings = investigation?.findings ?? null;
  const status = investigation?.status ?? "none";
  const failure = investigation?.status === "failed" ? failureHelp(investigation.failure?.code ?? "") : null;

  return (
    <section id="findings" aria-labelledby="ev-heading" className="card scroll-mt-24 p-5">
      <h2 id="ev-heading" className="flex items-center gap-2 text-base font-semibold">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <Icon name="spark" className="h-4 w-4" />
        </span>
        Findings and evidence
      </h2>

      {!findings ? (
        <p className="mt-3 text-sm text-muted">
          {status === "running" && "Waiting for the investigation to finish…"}
          {failure && `No findings: ${failure.title} ${failure.next}`}
          {status === "none" && "No findings yet. Run an investigation to see evidence-backed results."}
        </p>
      ) : (
        <div className="enter mt-4 space-y-5 text-sm">
          <div
            className="rounded-2xl border border-border p-5"
            aria-label="Short answer"
            style={{ background: "linear-gradient(135deg, var(--accent-soft), var(--surface-2) 70%)" }}
          >
            <Heading>Most likely cause</Heading>
            <p className="mt-1.5 text-lg font-semibold leading-snug">{findings.root_cause_hypothesis}</p>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <ConfidenceMeter level={findings.confidence} />
              <span
                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${confidenceStyle[findings.confidence]}`}
              >
                Confidence: {findings.confidence}.
              </span>
              <span className="text-muted">{CONFIDENCE_MEANING[findings.confidence]}</span>
            </div>
            <p className="mt-3 text-muted">{findings.confidence_rationale}</p>
            {findings.confidence_lowered_from && (
              <p
                className="mt-3 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted"
                title="The agent claimed more than the metrics and deployments it looked at support, so the system lowered it."
              >
                The agent first claimed <span className="font-medium capitalize">{findings.confidence_lowered_from}</span>{" "}
                confidence; the system lowered it because the evidence gathered does not support more.
              </p>
            )}
            <div className="mt-4 rounded-xl border border-accent/30 bg-surface px-4 py-3">
              <Heading>What to do next</Heading>
              <p className="mt-1">{findings.recommended_next_step}</p>
            </div>
          </div>

          <div>
            <Heading>What the agent found</Heading>
            <p className="mt-1">{findings.summary}</p>
          </div>

          <div>
            <Heading>Evidence ({findings.evidence.length})</Heading>
            <p className="mt-1 text-xs text-muted">
              Each item points at a log line, metric or release the agent actually looked at in this run.
            </p>
            <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border">
              {findings.evidence.map((e) => (
                <li key={e.reference_id} className="px-3.5 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-md bg-accent-soft px-1.5 py-0.5 text-xs font-medium text-accent">
                      {sourceLabel[e.source]}
                    </span>
                    <code className="break-all font-mono text-xs text-muted">{e.reference_id}</code>
                  </div>
                  <p className="mt-1">{e.observation}</p>
                </li>
              ))}
            </ul>
          </div>

          {findings.alternative_hypotheses.length > 0 && (
            <div>
              <Heading>Other possibilities and unknowns</Heading>
              <ul className="mt-1.5 list-disc space-y-1 pl-5">
                {findings.alternative_hypotheses.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </div>
          )}

          {findings.limitations.length > 0 && (
            <div className="rounded-xl bg-surface-2 px-4 py-3">
              <Heading>What this answer cannot tell you</Heading>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-muted">
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
