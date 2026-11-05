"use client";

import Link from "next/link";
import NotFound from "@/app/not-found";
import { formatDateTime, formatWindow } from "@/lib/format";
import { useAuth } from "@/lib/use-auth";
import { useWorkspace } from "@/lib/use-workspace";
import { AccessCode } from "../AccessCode";
import { SeverityBadge, StatusBadge, severityColorOf } from "../badges";
import { Note, Skeleton } from "../ui";
import { EvidencePanel } from "./EvidencePanel";
import { FeedbackBox } from "./FeedbackBox";
import { InvestigationPanel } from "./InvestigationPanel";
import { RemediationPanel } from "./RemediationPanel";
import { Timeline } from "./Timeline";

function Loading() {
  return (
    <div aria-busy="true" className="space-y-4">
      <Skeleton className="h-40" label="Loading incident" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Skeleton className="h-56" />
          <Skeleton className="h-40" />
        </div>
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}

export function IncidentWorkspace({ incidentId }: { incidentId: string }) {
  const w = useWorkspace(incidentId);
  const auth = useAuth();

  if (w.load === "loading") return <Loading />;
  if (w.load === "not_found") return <NotFound />;
  if (w.load === "error" || !w.incident) {
    return (
      <div className="space-y-3">
        <Note tone="danger" alert title="Could not load this incident">
          {w.loadError}
        </Note>
        <Link href="/" className="btn btn-secondary">
          Back to incidents
        </Link>
      </div>
    );
  }

  const incident = w.incident;
  const live = incident.id.startsWith("LIVE-");

  return (
    <div className="space-y-4">
      {live && (
        <Note tone="info" title="Your AWS account (live)">
          This is real data, read through your read-only role. AgentOps cannot change anything there, so it suggests next
          steps but offers no action to run.
        </Note>
      )}

      <header className="card relative overflow-hidden p-5 pl-6">
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 w-1.5"
          style={{ backgroundColor: severityColorOf(incident.severity) }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted">{incident.id}</span>
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={incident.status} />
        </div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">{incident.title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">{incident.summary}</p>
        <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Service", <span key="s" className="font-mono">{incident.service}</span>],
            ["Started", formatDateTime(incident.started_at)],
            ["Investigation window", formatWindow(incident.started_at, incident.window_end)],
            ...incident.signals.map((s) => [s.label, s.value] as const),
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-xl bg-surface-2 px-3 py-2">
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="mt-0.5">{value}</dd>
            </div>
          ))}
        </dl>
      </header>

      {w.needsCode && <AccessCode onSaved={w.codeAccepted} />}

      {w.actionError && (
        <Note tone="danger" alert>
          {w.actionError}
        </Note>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start">
        <div className="space-y-4">
          <InvestigationPanel
            investigation={w.investigation}
            busy={w.busy}
            onStart={() => void w.start()}
            auth={auth}
            onSignIn={() => auth.signIn()}
          />
          <EvidencePanel investigation={w.investigation} />
          <FeedbackBox
            investigation={w.investigation}
            busy={w.busy}
            onSend={(helpful, comment) => void w.sendFeedback(helpful, comment)}
          />
          {!live && (
            <RemediationPanel
              investigation={w.investigation}
              busy={w.busy}
              onApprove={() => void w.approve()}
              onReject={() => void w.reject()}
            />
          )}
        </div>
        <div className="lg:sticky lg:top-24">
          <Timeline events={w.timeline} />
        </div>
      </div>
    </div>
  );
}
