import Link from "next/link";
import { notFound } from "next/navigation";
import { IncidentWorkspace } from "@/components/incident/IncidentWorkspace";
import { getIncident, incidents, mockInvestigations } from "@/lib/mock-data";

export function generateStaticParams() {
  return incidents.map((i) => ({ id: i.id }));
}

export default async function IncidentPage(props: PageProps<"/incidents/[id]">) {
  const { id } = await props.params;
  const incident = getIncident(id);
  const script = mockInvestigations[id];
  if (!incident || !script) notFound();

  return (
    <div className="space-y-3">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link href="/" className="hover:text-foreground hover:underline">
          Incidents
        </Link>
        <span aria-hidden="true"> / </span>
        <span>{incident.id}</span>
      </nav>
      <IncidentWorkspace incident={incident} script={script} />
    </div>
  );
}
