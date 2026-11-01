import Link from "next/link";
import { IncidentWorkspace } from "@/components/incident/IncidentWorkspace";
import { INCIDENT_IDS } from "@/lib/incident-ids";

export function generateStaticParams() {
  return INCIDENT_IDS.map((id) => ({ id }));
}

export default async function IncidentPage(props: PageProps<"/incidents/[id]">) {
  const { id } = await props.params;

  return (
    <div className="space-y-3">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/" className="hover:text-foreground hover:underline">
          Incidents
        </Link>
        <span aria-hidden="true"> / </span>
        <span>{id}</span>
      </nav>
      <IncidentWorkspace incidentId={id} />
    </div>
  );
}
