"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth, useAwsConnectAvailable } from "@/lib/use-auth";

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  const path = usePathname() ?? "";
  const active = href === "/" ? path === "/" || path.startsWith("/incidents") : path.startsWith(href.replace(/\/$/, ""));
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
        active ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
      }`}
    >
      {children}
    </Link>
  );
}

/** Navigation, plus sign in / sign out when the API requires sign-in. */
export function AccountMenu() {
  const auth = useAuth();
  const canConnect = useAwsConnectAvailable();
  const signedIn = auth.status === "signed_in";

  return (
    <nav aria-label="Main" className="flex flex-wrap items-center gap-1">
      <NavLink href="/">Incidents</NavLink>
      {signedIn && <NavLink href="/investigations/">My investigations</NavLink>}
      {signedIn && canConnect && <NavLink href="/connect/">Connect AWS</NavLink>}

      {auth.status === "unavailable" && <p className="px-2 text-xs text-muted">Sign-in is not set up on this site.</p>}

      {signedIn && (
        <div className="ml-1 flex items-center gap-2 border-l border-border pl-3">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold uppercase text-accent"
          >
            {(auth.email ?? "?").charAt(0)}
          </span>
          <span className="hidden max-w-[14rem] truncate text-sm text-muted md:inline" title={auth.email ?? undefined}>
            {auth.email ?? "Signed in"}
          </span>
          <button type="button" onClick={auth.signOut} className="btn btn-secondary btn-sm">
            Sign out
          </button>
        </div>
      )}

      {auth.status === "signed_out" && (
        <div className="ml-1 flex items-center gap-2">
          {auth.error && (
            <span role="alert" className="text-xs text-danger">
              {auth.error}
            </span>
          )}
          <button type="button" onClick={() => auth.signIn()} className="btn btn-primary btn-sm">
            Sign in
          </button>
        </div>
      )}
    </nav>
  );
}
