"use client";

import { useEffect, useState } from "react";
import { api, type PublicConfig } from "@/lib/api";

const LABELS: Record<PublicConfig["data_mode"] | "unknown", { text: string; title: string }> = {
  unknown: { text: "Demo telemetry · not real production data", title: "Loading data source…" },
  simulated: {
    text: "Simulated demo telemetry · not real production data",
    title: "All incidents, logs and metrics shown are fictional demo data.",
  },
  captured: {
    text: "Telemetry captured from a lab system · not production data",
    title:
      "Logs and metrics were measured from a small real system with faults injected (time-compressed). It is not production data.",
  },
};

/** Says honestly where the incident data comes from, as reported by the API this page is talking to. */
export function DataModeBadge() {
  const [mode, setMode] = useState<PublicConfig["data_mode"] | "unknown">("unknown");

  useEffect(() => {
    const ctl = new AbortController();
    api
      .config(ctl.signal)
      .then((c) => setMode(c.data_mode))
      .catch(() => {
        /* the dashboard reports an unreachable API itself; keep the neutral label */
      });
    return () => ctl.abort();
  }, []);

  const label = LABELS[mode];
  return (
    <p className="flex items-center gap-2 text-xs text-muted" title={label.title}>
      <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
      {label.text}
    </p>
  );
}
