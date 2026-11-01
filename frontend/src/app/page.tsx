import { IncidentDashboard } from "@/components/IncidentDashboard";

export default function DashboardPage() {
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted">
        Select an incident to run an investigation. An agent calls read-only diagnostic tools against the incident&apos;s
        logs, metrics and deployments and proposes an evidence-backed cause; nothing runs until you approve it.
      </p>
      <IncidentDashboard />
    </div>
  );
}
