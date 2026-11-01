import Link from "next/link";
import { DataModeBadge } from "./DataModeBadge";

export function AppHeader() {
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-6">
        <Link href="/" className="text-base font-semibold tracking-tight">
          AgentOps AI
        </Link>
        <DataModeBadge />
      </div>
    </header>
  );
}
