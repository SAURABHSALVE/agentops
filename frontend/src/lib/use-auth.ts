"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import {
  SignInError,
  getSession,
  loadAuthConfig,
  sessionEmail,
  signIn,
  signOut,
  subscribeSession,
  type Session,
} from "./auth";

export type AuthState =
  | { status: "loading" }
  /** The API runs without sign-in (local, or deployed with auth switched off): nothing to show. */
  | { status: "off" }
  /** Sign-in is required but this site has no usable sign-in settings. */
  | { status: "unavailable" }
  | { status: "signed_out" }
  | { status: "signed_in"; email: string | null };

type Mode = "unknown" | "none" | "cognito";

let modePromise: Promise<Mode> | null = null;

/** Whether the API wants sign-in. Asked once per page load and shared by every component that needs it. */
function loadMode(): Promise<Mode> {
  modePromise ??= api
    .config()
    .then((c): Mode => (c.auth?.mode === "cognito" ? "cognito" : "none"))
    .catch((): Mode => "unknown");
  return modePromise;
}

export function useAuth(): AuthState & { error: string | null; signIn: (returnTo?: string) => void; signOut: () => void } {
  const [mode, setMode] = useState<Mode | "loading">("loading");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void loadMode().then((m) => alive && setMode(m));
    void loadAuthConfig().then((c) => alive && setConfigured(c !== null));
    const refresh = () => setSession(getSession());
    refresh();
    const off = subscribeSession(refresh);
    return () => {
      alive = false;
      off();
    };
  }, []);

  const startSignIn = useCallback((returnTo?: string) => {
    setError(null);
    signIn(returnTo ?? `${window.location.pathname}${window.location.search}`).catch((e: unknown) =>
      setError(e instanceof SignInError ? e.message : "Could not start sign-in. Try again."),
    );
  }, []);
  const startSignOut = useCallback(() => void signOut(), []);

  let state: AuthState;
  if (mode === "loading") state = { status: "loading" };
  else if (mode !== "cognito") state = { status: "off" }; // nothing else to wait for
  else if (configured === null) state = { status: "loading" };
  else if (!configured) state = { status: "unavailable" };
  else if (session) state = { status: "signed_in", email: sessionEmail(session) };
  else state = { status: "signed_out" };

  return { ...state, error, signIn: startSignIn, signOut: startSignOut };
}
