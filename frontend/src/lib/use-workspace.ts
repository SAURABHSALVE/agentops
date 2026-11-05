"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type PublicConfig } from "./api";
import { friendlyError } from "./plain";
import type { Incident, Investigation, TimelineEvent } from "./types";

const POLL_MS = 1000;

export type Load = "loading" | "ready" | "not_found" | "error";
export type Busy = "starting" | "approving" | "rejecting" | "feedback" | null;

function message(e: unknown): string {
  return e instanceof ApiError ? friendlyError(e.code, e.message) : "Something went wrong talking to the API.";
}

/** Everything the incident page shows, all from the API: the incident, the newest investigation (polled
 *  while it runs), and the audit timeline. Nothing is simulated in the browser. */
export function useWorkspace(incidentId: string) {
  const [load, setLoad] = useState<Load>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [busy, setBusy] = useState<Busy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [needsCode, setNeedsCode] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  /** Re-read the incident (its status follows the investigation) and the timeline. */
  const refreshAux = useCallback(async () => {
    try {
      const [inc, tl] = await Promise.all([api.getIncident(incidentId), api.getTimeline(incidentId)]);
      if (alive.current) {
        setIncident(inc);
        setTimeline(tl);
      }
    } catch {
      /* the main view keeps what it has; the next refresh retries */
    }
  }, [incidentId]);

  // Initial load.
  useEffect(() => {
    const ctl = new AbortController();
    (async () => {
      try {
        api
          .config(ctl.signal)
          .then((c) => {
            if (!ctl.signal.aborted) {
              setConfig(c);
              if (c.write_access === "code_required") setNeedsCode(true);
            }
          })
          .catch(() => undefined); // optional: the page works without it
        const [inc, tl] = await Promise.all([api.getIncident(incidentId, ctl.signal), api.getTimeline(incidentId, ctl.signal)]);
        const latest = await api.latestInvestigation(incidentId, ctl.signal);
        if (ctl.signal.aborted) return;
        setIncident(inc);
        setTimeline(tl);
        setInvestigation(latest);
        setLoad("ready");
      } catch (e) {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404) setLoad("not_found");
        else {
          setLoadError(message(e));
          setLoad("error");
        }
      }
    })();
    return () => ctl.abort();
  }, [incidentId]);

  // Poll the running investigation until it stops running.
  const runningId = investigation?.status === "running" ? investigation.id : null;
  useEffect(() => {
    if (!runningId) return;
    const ctl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const next = await api.getInvestigation(runningId, ctl.signal);
        if (ctl.signal.aborted) return;
        setInvestigation(next);
        void refreshAux();
        if (next.status === "running") timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (ctl.signal.aborted) return;
        setActionError(message(e));
      }
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [runningId, refreshAux]);

  const run = useCallback(
    async (kind: Exclude<Busy, null>, call: () => Promise<Investigation>) => {
      setBusy(kind);
      setActionError(null);
      let failed = false;
      try {
        const next = await call();
        // Re-read the timeline and incident first, so the outcome and its history appear together instead of the
        // history catching up a moment later.
        await refreshAux();
        if (alive.current) setInvestigation(next);
      } catch (e) {
        failed = true;
        if (alive.current) {
          if (e instanceof ApiError && e.status === 401 && e.code === "unauthorized") {
            setNeedsCode(true);
            setActionError("An access code is required for this action. Enter it above, then try again.");
          } else setActionError(message(e));
        }
      } finally {
        if (failed) await refreshAux();
        if (alive.current) setBusy(null);
      }
    },
    [refreshAux],
  );

  return {
    load,
    loadError,
    incident,
    investigation,
    timeline,
    busy,
    actionError,
    config,
    needsCode,
    codeAccepted: () => {
      setNeedsCode(false);
      setActionError(null);
    },
    start: () => run("starting", () => api.startInvestigation(incidentId)),
    approve: () => investigation && run("approving", () => api.approve(investigation.id)),
    reject: () => investigation && run("rejecting", () => api.reject(investigation.id)),
    sendFeedback: (helpful: boolean, comment: string) =>
      investigation && run("feedback", () => api.feedback(investigation.id, helpful, comment)),
  };
}
