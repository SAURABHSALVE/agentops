import { IncidentList } from "@/components/IncidentList";
import { incidents } from "@/lib/mock-data";

export default function DashboardPage() {
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted">
        Select a simulated incident to run an investigation. In this build the investigation is
        scripted mock data; real Bedrock-driven tool calls arrive in a later phase.
      </p>
      <IncidentList incidents={incidents} />
    </div>
  );
}
