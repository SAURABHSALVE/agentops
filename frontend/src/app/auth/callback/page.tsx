"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { SignInError, completeSignIn } from "@/lib/auth";

/** Where Cognito sends people back to. Swaps the one-time code for a session, then returns to where they were. */
export default function AuthCallbackPage() {
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false); // the code works once, and development mode runs effects twice

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    completeSignIn(window.location.search)
      .then((returnTo) => window.location.replace(returnTo))
      .catch((e: unknown) =>
        setError(e instanceof SignInError ? e.message : "Sign-in could not be completed. Start again."),
      );
  }, []);

  if (error) {
    return (
      <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-6">
        <h1 className="text-lg font-semibold text-danger">Could not sign you in</h1>
        <p className="mt-1 text-sm text-muted">{error}</p>
        <Link href="/" className="mt-4 inline-block text-sm underline">
          Back to incidents
        </Link>
      </div>
    );
  }
  return (
    <p role="status" className="text-sm text-muted">
      Signing you in…
    </p>
  );
}
