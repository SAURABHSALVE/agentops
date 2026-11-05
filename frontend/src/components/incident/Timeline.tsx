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

const dotColor: Record<TimelineEvent["kind"], string> = {
  incident: "var(--muted)",
  agent: "var(--accent)",
  tool: "var(--border-strong)",
  approval: "var(--accent)",
  action: "var(--warn)",
  verification: "var(--ok)",
  error: "var(--danger)",
};

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <section aria-labelledby="tl-heading" className="card p-5">
      <h2 id="tl-heading" className="text-base font-semibold">
        Timeline
      </h2>
      <p className="mt-1 text-xs text-muted">
        Recorded by the server: the history survives page refreshes and backend restarts.
      </p>
      <ol className="relative mt-4 space-y-4 border-l border-border pl-5">
        {events.map((e) => (
          <li key={e.id} className="relative text-sm">
            <span
              aria-hidden="true"
              className="absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-[var(--surface)]"
              style={{ backgroundColor: dotColor[e.kind] }}
            />
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
