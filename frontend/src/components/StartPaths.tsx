"use client";

import Link from "next/link";
import { useAwsConnectAvailable } from "@/lib/use-auth";
import { Icon } from "./ui";

/** Where to start, when there is more than one way: the demo incidents below, or the visitor's own AWS account. */
export function StartPaths() {
  const canConnect = useAwsConnectAvailable();
  if (!canConnect) return null;

  return (
    <section aria-labelledby="paths-heading">
      <h2 id="paths-heading" className="eyebrow">
        Two ways to try it
      </h2>
      <div className="enter-stagger mt-3 grid gap-3 sm:grid-cols-2">
        <a href="#incidents-heading" className="card card-interactive group flex gap-4 p-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Icon name="list" />
          </span>
          <span>
            <span className="block font-medium">Try the demo incidents</span>
            <span className="mt-1 block text-sm text-muted">
              Ready-made problems. Nothing to set up, about two minutes.
            </span>
            <span className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-accent">
              Pick one
              <Icon name="arrow" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </span>
          </span>
        </a>
        <Link href="/connect/" className="card card-interactive group flex gap-4 p-5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Icon name="cloud" />
          </span>
          <span>
            <span className="block font-medium">Investigate your own AWS account</span>
            <span className="mt-1 block text-sm text-muted">
              Read-only access you create and can remove any time. Takes about five minutes.
            </span>
            <span className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-accent">
              Connect AWS
              <Icon name="arrow" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </span>
          </span>
        </Link>
      </div>
    </section>
  );
}
