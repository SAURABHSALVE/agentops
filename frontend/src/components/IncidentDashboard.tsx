"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { IncidentSummary } from "@/lib/types";
import { IncidentList } from "./IncidentList";

type State =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; incidents: IncidentSummary[] };

export function IncidentDashboard() {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctl = new AbortController();
    api
      .listIncidents(ctl.signal)
      .then((incidents) => setState({ phase: "ready", incidents }))
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setState({ phase: "error", message: e instanceof ApiError ? e.message : "Could not load incidents." });
      });
    return () => ctl.abort();
  }, [attempt]);

  if (state.phase === "loading") {
    return (
      <div role="status" aria-label="Loading incidents" className="space-y-2">
        <div className="h-20 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
        <div className="h-20 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
        <span className="sr-only">Loading incidents…</span>
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-6">
        <h1 className="text-lg font-semibold text-danger">Could not load incidents</h1>
        <p className="mt-1 text-sm text-muted">{state.message}</p>
        <button
          type="button"
          onClick={() => {
            setState({ phase: "loading" });
            setAttempt((n) => n + 1);
          }}
          className="mt-4 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg"
        >
          Try again
        </button>
      </div>
    );
  }

  return <IncidentList incidents={state.incidents} />;
}
