import { formatDate, formatTime } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";

const kindLabel: Record<TimelineEvent["kind"], string> = {
  incident: "Incident",
  agent: "Agent",
  tool: "Tool",
  approval: "Approval",
  action: "Action",
  verification: "Verification",
  error: "Error",
};

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <section aria-labelledby="tl-heading" className="rounded-lg border border-border bg-surface p-4">
      <h2 id="tl-heading" className="font-semibold">
        Timeline
      </h2>
      <p className="mt-1 text-xs text-muted">Session events are not saved yet; they reset on refresh.</p>
      <ol className="mt-3 space-y-3 border-l border-border pl-4">
        {events.map((e) => (
          <li key={e.id} className="text-sm">
            <p className="text-xs text-muted">
              {formatDate(e.at)} · {formatTime(e.at)} · {kindLabel[e.kind]}
            </p>
            <p className={e.kind === "error" ? "font-medium text-danger" : "font-medium"}>{e.title}</p>
            {e.detail && <p className="text-muted">{e.detail}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}
