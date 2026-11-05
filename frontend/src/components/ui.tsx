import type { ReactNode } from "react";

/** Small shared pieces: icons, a spinner, a skeleton, a tinted note and a step tracker. Styles live in globals.css. */

type IconName = "search" | "shield" | "plug" | "check" | "alert" | "info" | "arrow" | "cloud" | "spark" | "list" | "lock";

const PATHS: Record<IconName, ReactNode> = {
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  shield: <path d="M12 3 5 6v5.5c0 4.2 2.8 7.4 7 9.5 4.2-2.1 7-5.3 7-9.5V6l-7-3Zm-2.5 9 2 2 3.5-4" />,
  plug: <path d="M9 3v5m6-5v5M7 8h10v3a5 5 0 0 1-10 0V8Zm5 8v5" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: <path d="M12 8v5m0 3.5h.01M10.3 4.5 3.2 17a2 2 0 0 0 1.7 3h14.2a2 2 0 0 0 1.7-3L13.7 4.5a2 2 0 0 0-3.4 0Z" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5m0-8.5h.01" />
    </>
  ),
  arrow: <path d="M5 12h14m-5.5-5.5L19 12l-5.5 5.5" />,
  cloud: <path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.5 8.5H7Z" />,
  spark: <path d="M12 3v4m0 10v4M3 12h4m10 0h4M6.3 6.3l2.8 2.8m5.8 5.8 2.8 2.8m0-11.4-2.8 2.8m-5.8 5.8-2.8 2.8" />,
  list: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
};

export function Icon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}

/** A turning ring. Decorative: put the meaning in nearby text. */
export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`spinner ${className}`} aria-hidden="true" />;
}

/** A button label that shows a spinner while `busy`. */
export function Busy({ busy, children }: { busy: boolean; children: ReactNode }) {
  return (
    <>
      {busy && <Spinner />}
      {children}
    </>
  );
}

export function Skeleton({ className = "h-24", label }: { className?: string; label?: string }) {
  return (
    <div role={label ? "status" : undefined} aria-label={label} className={`skeleton ${className}`}>
      {label && <span className="sr-only">{label}…</span>}
    </div>
  );
}

type Tone = "info" | "ok" | "warn" | "danger";

const TONES: Record<Tone, { box: string; icon: IconName; color: string }> = {
  info: { box: "border-accent/30 bg-accent-soft", icon: "info", color: "text-accent" },
  ok: { box: "border-ok/35 bg-ok/10", icon: "check", color: "text-ok" },
  warn: { box: "border-warn/35 bg-warn/10", icon: "alert", color: "text-warn" },
  danger: { box: "border-danger/35 bg-danger/10", icon: "alert", color: "text-danger" },
};

/** A tinted note with an icon. Use `role="alert"` through `alert` for things that need attention right now. */
export function Note({
  tone = "info",
  title,
  children,
  alert = false,
  className = "",
}: {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
  alert?: boolean;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <div role={alert ? "alert" : "note"} className={`flex gap-3 rounded-xl border px-4 py-3 text-sm ${t.box} ${className}`}>
      <Icon name={t.icon} className={`mt-0.5 h-5 w-5 shrink-0 ${t.color}`} />
      <div className="min-w-0">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={title ? "mt-0.5 text-muted" : ""}>{children}</div>}
      </div>
    </div>
  );
}

export interface StepInfo {
  label: string;
  hint?: string;
}

/** A horizontal tracker: steps before `current` are done, `current` is highlighted, the rest wait. */
export function Stepper({ steps, current, label }: { steps: StepInfo[]; current: number; label: string }) {
  return (
    <ol aria-label={label} className="grid gap-2 sm:grid-flow-col sm:auto-cols-fr">
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li
            key={s.label}
            aria-current={active ? "step" : undefined}
            className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors ${
              active ? "border-accent/50 bg-accent-soft" : done ? "border-ok/30 bg-ok/5" : "border-border bg-surface"
            }`}
          >
            <span
              className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                done ? "pop bg-ok text-white" : active ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted"
              }`}
            >
              {done ? <Icon name="check" className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className="min-w-0">
              <span className={`block text-sm font-medium ${active || done ? "" : "text-muted"}`}>{s.label}</span>
              {s.hint && <span className="block text-xs text-muted">{s.hint}</span>}
              <span className="sr-only">{done ? " (done)" : active ? " (current step)" : " (not started)"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Three bars that fill with confidence: a glance before the words. */
export function ConfidenceMeter({ level }: { level: "high" | "medium" | "low" }) {
  const filled = level === "high" ? 3 : level === "medium" ? 2 : 1;
  const color = level === "high" ? "bg-ok" : level === "medium" ? "bg-warn" : "bg-danger";
  return (
    <span className="flex gap-1" aria-hidden="true">
      {[1, 2, 3].map((n) => (
        <span key={n} className={`h-1.5 w-7 rounded-full ${n <= filled ? color : "bg-border-strong/60"}`} />
      ))}
    </span>
  );
}
