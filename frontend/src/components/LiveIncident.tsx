"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import NotFound from "@/app/not-found";
import { IncidentWorkspace } from "./incident/IncidentWorkspace";

const LIVE_ID = /^LIVE-[0-9a-f]{12}$/;

/** One alarm from the signed-in user's own AWS account. The id comes from the address bar, so it is checked here and
 *  again by the API, which only serves it to the user whose account it came from. */
export function LiveIncident() {
  const id = useSearchParams().get("id") ?? "";
  if (!LIVE_ID.test(id)) return <NotFound />;
  return (
    <div className="space-y-3">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/connect/" className="hover:text-foreground hover:underline">
          Connect AWS
        </Link>
        <span aria-hidden="true"> / </span>
        <span>{id}</span>
      </nav>
      <IncidentWorkspace incidentId={id} />
    </div>
  );
}
