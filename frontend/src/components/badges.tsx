import type { IncidentStatus, Severity } from "@/lib/types";

const severityColor: Record<Severity, string> = {
  critical: "var(--sev-critical)",
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
};

const statusLabel: Record<IncidentStatus, string> = {
  open: "Open",
  investigating: "Investigating",
  mitigated: "Mitigated",
  resolved: "Resolved",
};

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium capitalize"
      style={{
        color,
        borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)`,
      }}
    >
      {children}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Pill color={severityColor[severity]}>{severity}</Pill>;
}

export function StatusBadge({ status }: { status: IncidentStatus }) {
  const color = status === "resolved" ? "var(--ok)" : status === "investigating" ? "var(--accent)" : "var(--muted)";
  return <Pill color={color}>{statusLabel[status]}</Pill>;
}
