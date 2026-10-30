"use client";

import Link from "next/link";
import NotFound from "@/app/not-found";
import { formatDateTime, formatWindow } from "@/lib/format";
import { useWorkspace } from "@/lib/use-workspace";
import { SeverityBadge, StatusBadge } from "../badges";
import { EvidencePanel } from "./EvidencePanel";
import { InvestigationPanel } from "./InvestigationPanel";
import { RemediationPanel } from "./RemediationPanel";
import { Timeline } from "./Timeline";

function Skeleton() {
  return (
    <div role="status" aria-label="Loading incident" className="space-y-4">
      <div className="h-32 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      <div className="h-56 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      <span className="sr-only">Loading incident…</span>
    </div>
  );
}

export function IncidentWorkspace({ incidentId }: { incidentId: string }) {
  const w = useWorkspace(incidentId);

  if (w.load === "loading") return <Skeleton />;
  if (w.load === "not_found") return <NotFound />;
  if (w.load === "error" || !w.incident) {
    return (
      <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-6">
        <h1 className="text-lg font-semibold text-danger">Could not load this incident</h1>
        <p className="mt-1 text-sm text-muted">{w.loadError}</p>
        <Link href="/" className="mt-4 inline-block text-sm underline">
          Back to incidents
        </Link>
      </div>
    );
  }

  const incident = w.incident;

  return (
    <div className="space-y-4">
      <header className="rounded-lg border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted">{incident.id}</span>
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={incident.status} />
        </div>
        <h1 className="mt-2 text-xl font-semibold tracking-tight">{incident.title}</h1>
        <p className="mt-1 text-sm text-muted">{incident.summary}</p>
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-muted">Service</dt>
            <dd className="font-mono">{incident.service}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Started</dt>
            <dd>{formatDateTime(incident.started_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Investigation window</dt>
            <dd>{formatWindow(incident.started_at, incident.window_end)}</dd>
          </div>
          {incident.signals.map((s) => (
            <div key={s.label}>
              <dt className="text-xs text-muted">{s.label}</dt>
              <dd>{s.value}</dd>
            </div>
          ))}
        </dl>
      </header>

      {w.actionError && (
        <p role="alert" className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {w.actionError}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <InvestigationPanel investigation={w.investigation} busy={w.busy} onStart={() => void w.start()} />
          <EvidencePanel investigation={w.investigation} />
          <RemediationPanel
            investigation={w.investigation}
            busy={w.busy}
            onApprove={() => void w.approve()}
            onReject={() => void w.reject()}
          />
        </div>
        <Timeline events={w.timeline} />
      </div>
    </div>
  );
}
