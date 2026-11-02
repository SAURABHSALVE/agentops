"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { outcomeText } from "@/lib/plain";
import type { InvestigationSummary } from "@/lib/types";
import { useAuth } from "@/lib/use-auth";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; rows: InvestigationSummary[] };

const TONE = { ok: "text-ok", bad: "font-medium text-danger", plain: "text-muted" } as const;

/** The newest investigation of every incident the signed-in user has investigated, with a way to delete them all. */
export function MyInvestigations() {
  const auth = useAuth();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const ready = auth.status === "signed_in" || auth.status === "off";

  const [reloads, setReloads] = useState(0);
  const reload = () => {
    setLoad({ state: "loading" });
    setReloads((n) => n + 1);
  };

  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    api
      .myInvestigations(ctl.signal)
      .then((rows) => setLoad({ state: "ready", rows }))
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setLoad({
          state: "error",
          message: e instanceof ApiError && e.status === 0 ? e.message : "Could not load your investigations.",
        });
      });
    return () => ctl.abort();
  }, [ready, reloads]);

  async function deleteAll() {
    setDeleting(true);
    setProblem(null);
    try {
      const { deleted } = await api.deleteMyInvestigations();
      setNotice(
        deleted === 1
          ? "Deleted 1 investigation. Your sign-in account is unchanged."
          : `Deleted ${deleted} investigations. Your sign-in account is unchanged.`,
      );
      setConfirming(false);
      reload();
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "Could not delete. Try again.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">My investigations</h1>
        <p className="mt-1 text-sm text-muted">
          The most recent investigation of each incident you have looked into. They are private to your account and are
          removed automatically after 7 days.
        </p>
      </div>

      {auth.status === "loading" && (
        <div className="h-24 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
      )}

      {auth.status === "signed_out" && (
        <div className="rounded-lg border border-border bg-surface p-5">
          <p className="font-medium">Sign in to see your investigations.</p>
          <p className="mt-1 text-sm text-muted">Anyone can read the incidents; what you run is saved to your account.</p>
          <button
            type="button"
            onClick={() => auth.signIn("/investigations/")}
            className="mt-3 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg"
          >
            Sign in
          </button>
        </div>
      )}

      {auth.status === "unavailable" && (
        <p className="rounded-lg border border-border bg-surface p-5 text-sm text-muted">
          Sign-in is required here but is not set up on this site.
        </p>
      )}

      {ready && load.state === "loading" && (
        <div
          role="status"
          aria-label="Loading your investigations"
          className="h-24 rounded-lg border border-border bg-surface motion-safe:animate-pulse"
        />
      )}

      {ready && load.state === "error" && (
        <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-5 text-sm">
          <p className="font-medium text-danger">{load.message}</p>
          <button type="button" onClick={reload} className="mt-2 text-sm underline">
            Try again
          </button>
        </div>
      )}

      {notice && (
        <p role="status" className="rounded border border-border bg-surface px-3 py-2 text-sm">
          {notice}
        </p>
      )}

      {ready && load.state === "ready" && load.rows.length === 0 && (
        <div className="rounded-lg border border-dashed border-border bg-surface p-6 text-center">
          <p className="font-medium">You have not run an investigation yet.</p>
          <p className="mt-1 text-sm text-muted">Pick an incident, press Investigate, and it will appear here.</p>
          <Link href="/" className="mt-3 inline-block rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg">
            Choose an incident
          </Link>
        </div>
      )}

      {ready && load.state === "ready" && load.rows.length > 0 && (
        <>
          <ul
            aria-label="Your investigations"
            className="divide-y divide-border rounded-lg border border-border bg-surface"
          >
            {load.rows.map((r) => {
              const outcome = outcomeText(r);
              return (
                <li key={r.id} className="p-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <Link href={`/incidents/${r.incident_id}`} className="font-medium hover:underline">
                      <span className="font-mono text-xs text-muted">{r.incident_id}</span> {r.incident_title}
                    </Link>
                    <span className="text-xs text-muted">{formatDateTime(r.started_at)}</span>
                  </div>
                  <p className={`mt-1 text-sm ${TONE[outcome.tone]}`}>{outcome.text}</p>
                  <p className="mt-1 text-xs text-muted">
                    {r.confidence ? `Confidence: ${r.confidence}` : "No answer yet"}
                    {r.helpful !== null && ` · You rated it ${r.helpful ? "helpful" : "not helpful"}`}
                    <span className="font-mono"> · {r.id}</span>
                  </p>
                </li>
              );
            })}
          </ul>

          <section aria-labelledby="delete-heading" className="rounded-lg border border-border bg-surface p-4">
            <h2 id="delete-heading" className="text-sm font-semibold">
              Delete my investigations
            </h2>
            <p className="mt-1 text-sm text-muted">
              Removes these investigations, your approvals and feedback, and resets any simulated fixes you applied. Your
              sign-in account stays.
            </p>
            {problem && (
              <p role="alert" className="mt-2 text-sm text-danger">
                {problem}
              </p>
            )}
            {!confirming ? (
              <button
                type="button"
                onClick={() => {
                  setNotice(null);
                  setConfirming(true);
                }}
                className="mt-3 rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium hover:border-danger"
              >
                Delete…
              </button>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-sm">
                  Delete {load.rows.length === 1 ? "this investigation" : `all ${load.rows.length} investigations`}? This
                  cannot be undone.
                </span>
                <button
                  type="button"
                  disabled={deleting}
                  onClick={() => void deleteAll()}
                  className="rounded bg-danger px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {deleting ? "Deleting…" : "Yes, delete"}
                </button>
                <button
                  type="button"
                  disabled={deleting}
                  onClick={() => setConfirming(false)}
                  className="rounded border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium"
                >
                  Cancel
                </button>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
