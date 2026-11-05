import Link from "next/link";
import { AccountMenu } from "./AccountMenu";
import { DataModeBadge } from "./DataModeBadge";

function Logo() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center rounded-[10px] text-white shadow-sm"
      style={{ background: "linear-gradient(135deg, #6366f1 0%, #8b5cf6 55%, #ec4899 120%)" }}
    >
      <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 13h4l2.5-6 4 11 2.5-5H21" />
      </svg>
    </span>
  );
}

export function AppHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border backdrop-blur-md" style={{ background: "var(--header-bg)" }}>
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5 rounded-lg text-[15px] font-semibold tracking-tight">
          <Logo />
          AgentOps AI
        </Link>
        <AccountMenu />
      </div>
      <div className="border-t border-border/60 bg-surface-2/60">
        <div className="mx-auto w-full max-w-6xl px-4 py-1 sm:px-6">
          <DataModeBadge />
        </div>
      </div>
    </header>
  );
}
