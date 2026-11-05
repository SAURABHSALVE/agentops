"use client";

import { useEffect } from "react";
import { Note } from "@/components/ui";

export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    // Console only; no external error reporting in the mock build.
    console.error(error);
  }, [error]);

  return (
    <div className="space-y-3">
      <Note tone="danger" alert title="Something went wrong">
        The page could not be displayed. You can try again.
      </Note>
      <button type="button" onClick={() => retry()} className="btn btn-primary">
        Try again
      </button>
    </div>
  );
}
