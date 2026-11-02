"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type PublicConfig } from "./api";
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

let configPromise: Promise<PublicConfig | null> | null = null;

/** The API's public settings. Asked once per page load and shared by every component that needs them. */
function loadConfig(): Promise<PublicConfig | null> {
  configPromise ??= api.config().catch(() => null);
  return configPromise;
}

/** Whether the API wants sign-in. */
function loadMode(): Promise<Mode> {
  return loadConfig().then((c): Mode => (c === null ? "unknown" : c.auth?.mode === "cognito" ? "cognito" : "none"));
}

/** Whether this site offers "connect your AWS account" (it is off unless the operator switched it on). */
export function useAwsConnectAvailable(): boolean {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    let alive = true;
    void loadConfig().then((c) => alive && setAvailable(c?.aws_connect?.available === true));
    return () => {
      alive = false;
    };
  }, []);
  return available;
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
