"use client";

import { useEffect } from "react";

export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    // Console only; no external error reporting in the mock build.
    console.error(error);
  }, [error]);

  return (
    <div role="alert" className="rounded-lg border border-danger/40 bg-danger/10 p-6">
      <h1 className="text-lg font-semibold text-danger">Something went wrong</h1>
      <p className="mt-1 text-sm text-muted">The page could not be displayed. You can try again.</p>
      <button
        type="button"
        onClick={() => retry()}
        className="mt-4 rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg"
      >
        Try again
      </button>
    </div>
  );
}
