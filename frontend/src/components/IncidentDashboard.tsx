"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { IncidentSummary } from "@/lib/types";
import { IncidentList } from "./IncidentList";
import { Note, Skeleton } from "./ui";

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
      <div className="space-y-2" aria-busy="true">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-20" label="Loading incidents" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div>
        <Note tone="danger" alert title="Could not load incidents">
          {state.message}
        </Note>
        <button
          type="button"
          onClick={() => {
            setState({ phase: "loading" });
            setAttempt((n) => n + 1);
          }}
          className="btn btn-primary mt-3"
        >
          Try again
        </button>
      </div>
    );
  }

  return <IncidentList incidents={state.incidents} />;
}
