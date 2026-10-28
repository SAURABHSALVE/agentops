import Link from "next/link";

export function AppHeader() {
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-6">
        <Link href="/" className="text-base font-semibold tracking-tight">
          AgentOps AI
        </Link>
        <p
          className="rounded border border-border bg-surface-2 px-2 py-1 text-xs text-muted"
          title="All incidents, logs and metrics shown are fictional demo data."
        >
          Simulated demo telemetry · not real production data
        </p>
      </div>
    </header>
  );
}
