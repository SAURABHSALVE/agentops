"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_HINTS } from "@/lib/plain";
import { useAuth } from "@/lib/use-auth";
import { Icon } from "./ui";

/** For a signed-in person: a first-run nudge toward the best starting incident, or a pointer back to their work. */
export function Welcome() {
  const auth = useAuth();
  const [mine, setMine] = useState<number | null>(null);
  const [start, setStart] = useState<{ id: string; title: string } | null>(null);
  const signedIn = auth.status === "signed_in";

  useEffect(() => {
    if (!signedIn) return;
    const ctl = new AbortController();
    api
      .myInvestigations(ctl.signal)
      .then((rows) => setMine(rows.length))
      .catch(() => undefined); // a convenience: the page works without it
    api
      .listIncidents(ctl.signal)
      .then((list) => {
        const pick = list.find((i) => DEMO_HINTS[i.id]?.tag === "Start here") ?? list[0];
        if (pick) setStart({ id: pick.id, title: pick.title });
      })
      .catch(() => undefined);
    return () => ctl.abort();
  }, [signedIn]);

  if (!signedIn || mine === null) return null;

  if (mine === 0) {
    return (
      <section
        aria-label="Welcome"
        className="card enter flex flex-wrap items-center justify-between gap-4 border-accent/35 p-5"
        style={{ background: "linear-gradient(120deg, var(--accent-soft), var(--surface) 60%)" }}
      >
        <div className="min-w-0">
          <p className="font-medium">You are signed in{auth.email ? ` as ${auth.email}` : ""}. Welcome!</p>
          <p className="mt-1 text-sm text-muted">
            Next step: run your first investigation. It takes about two minutes, and everything you do is private to
            your account.
          </p>
        </div>
        {start && (
          <Link href={`/incidents/${start.id}`} className="btn btn-primary group">
            Start with {start.id}: {start.title}
            <Icon name="arrow" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
        )}
      </section>
    );
  }

  return (
    <p className="text-sm text-muted">
      You have {mine === 1 ? "1 investigation" : `${mine} investigations`}.{" "}
      <Link href="/investigations" className="link">
        See them
      </Link>
    </p>
  );
}
