"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { formatDateTime } from "@/lib/format";
import { DEMO_HINTS } from "@/lib/plain";
import type { IncidentStatus, IncidentSummary, Severity } from "@/lib/types";
import { SeverityBadge, StatusBadge, severityColorOf } from "./badges";
import { Icon } from "./ui";

export function IncidentList({ incidents }: { incidents: IncidentSummary[] }) {
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [status, setStatus] = useState<IncidentStatus | "all">("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return incidents.filter(
      (i) =>
        (severity === "all" || i.severity === severity) &&
        (status === "all" || i.status === status) &&
        (q === "" || `${i.id} ${i.service} ${i.title}`.toLowerCase().includes(q)),
    );
  }, [incidents, query, severity, status]);

  const hasFilters = query !== "" || severity !== "all" || status !== "all";
  const clear = () => {
    setQuery("");
    setSeverity("all");
    setStatus("all");
  };

  return (
    <section aria-labelledby="incidents-heading" className="scroll-mt-24">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="incidents-heading" className="text-xl font-semibold tracking-tight">
            Incidents
          </h2>
          <p className="mt-0.5 text-sm text-muted">Pick one to see what the agent finds. Nothing runs until you press Investigate.</p>
        </div>
        <form
          role="search"
          aria-label="Filter incidents"
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => e.preventDefault()}
        >
          <label className="flex flex-col gap-1 text-xs text-muted">
            Search
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Service or title"
              className="field w-44"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            Severity
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Severity | "all")}
              className="field"
            >
              <option value="all">All</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as IncidentStatus | "all")}
              className="field"
            >
              <option value="all">All</option>
              <option value="open">Open</option>
              <option value="investigating">Investigating</option>
              <option value="mitigated">Mitigated</option>
              <option value="resolved">Resolved</option>
            </select>
          </label>
        </form>
      </div>

      <p className="mb-2 text-sm text-muted" aria-live="polite">
        Showing {filtered.length} of {incidents.length} incidents
      </p>

      {filtered.length === 0 ? (
        <div className="card border-dashed p-10 text-center">
          <span className="mx-auto flex h-10 w-10 items-center justify-center rounded-xl bg-surface-2 text-muted">
            <Icon name="search" />
          </span>
          <p className="mt-3 font-medium">No incidents match these filters.</p>
          <p className="mt-1 text-sm text-muted">Try a different search or clear the filters.</p>
          {hasFilters && (
            <button type="button" onClick={clear} className="btn btn-secondary mt-4">
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <ul className="enter-stagger space-y-2.5">
          {filtered.map((i) => (
            <li key={i.id}>
              <Link
                href={`/incidents/${i.id}`}
                className="card card-interactive group relative grid gap-1 overflow-hidden py-3.5 pl-5 pr-4 sm:grid-cols-[1fr_auto] sm:items-center sm:gap-4"
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-0 w-1"
                  style={{ backgroundColor: severityColorOf(i.severity) }}
                />
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted">{i.id}</span>
                    {DEMO_HINTS[i.id] && (
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">
                        {DEMO_HINTS[i.id].tag}
                      </span>
                    )}
                    <SeverityBadge severity={i.severity} />
                    <StatusBadge status={i.status} />
                  </span>
                  <span className="mt-1.5 block font-medium">{i.title}</span>
                  <span className="block text-sm text-muted">
                    <span className="font-mono">{i.service}</span>
                    {DEMO_HINTS[i.id] && <span> · {DEMO_HINTS[i.id].hint}</span>}
                  </span>
                </span>
                <span className="flex items-center gap-3 text-xs text-muted sm:text-right">
                  <span>
                    <span className="block">Updated</span>
                    <span className="block">{formatDateTime(i.updated_at)}</span>
                  </span>
                  <Icon
                    name="arrow"
                    className="hidden h-4 w-4 text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-accent sm:block"
                  />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
