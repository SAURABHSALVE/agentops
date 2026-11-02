"use client";

import Link from "next/link";
import { useAuth, useAwsConnectAvailable } from "@/lib/use-auth";

/** Sign in / sign out, shown only when the API requires sign-in. */
export function AccountMenu() {
  const auth = useAuth();
  const canConnect = useAwsConnectAvailable();
  if (auth.status === "loading" || auth.status === "off") return null;

  if (auth.status === "unavailable") {
    return <p className="text-xs text-muted">Sign-in is not set up on this site.</p>;
  }

  if (auth.status === "signed_in") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Link href="/investigations" className="text-sm underline hover:text-foreground">
          My investigations
        </Link>
        {canConnect && (
          <Link href="/connect" className="text-sm underline hover:text-foreground">
            Connect AWS
          </Link>
        )}
        <span className="max-w-[16rem] truncate text-muted" title={auth.email ?? undefined}>
          {auth.email ?? "Signed in"}
        </span>
        <button
          type="button"
          onClick={auth.signOut}
          className="rounded border border-border bg-surface-2 px-2.5 py-1 text-xs font-medium hover:border-accent"
        >
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {auth.error && (
        <span role="alert" className="text-xs text-danger">
          {auth.error}
        </span>
      )}
      <button
        type="button"
        onClick={() => auth.signIn()}
        className="rounded bg-accent px-3 py-1 text-sm font-medium text-accent-fg"
      >
        Sign in
      </button>
    </div>
  );
}
