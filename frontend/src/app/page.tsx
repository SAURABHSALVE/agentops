import { IncidentDashboard } from "@/components/IncidentDashboard";

export default function DashboardPage() {
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted">
        Select a simulated incident to run an investigation. An agent calls read-only diagnostic tools against
        simulated demo telemetry and proposes an evidence-backed cause; nothing runs until you approve it.
      </p>
      <IncidentDashboard />
    </div>
  );
}
