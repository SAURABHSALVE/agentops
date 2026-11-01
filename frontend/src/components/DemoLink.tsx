"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_HINTS } from "@/lib/plain";

/** The call-to-action. It links to an incident this API really serves (simulated and captured data have
 *  different ids), preferring the one tagged "Start here"; until the list loads it jumps to the list. */
export function DemoLink() {
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    api
      .listIncidents(ctl.signal)
      .then((list) => {
        const start = list.find((i) => DEMO_HINTS[i.id]?.tag === "Start here") ?? list[0];
        if (start) setTarget(start.id);
      })
      .catch(() => {
        /* the list below reports an unreachable API; the button stays a jump link */
      });
    return () => ctl.abort();
  }, []);

  const className = "rounded bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:opacity-90";
  return target ? (
    <Link href={`/incidents/${target}`} className={className}>
      Try the 2-minute demo
    </Link>
  ) : (
    <a href="#incidents-heading" className={className}>
      Try the 2-minute demo
    </a>
  );
}
