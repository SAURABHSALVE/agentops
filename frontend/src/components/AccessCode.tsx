"use client";

import { useState } from "react";
import { getAccessCode, setAccessCode } from "@/lib/api";

/** Shown only when the deployment protects writes with an access code. The code is kept in sessionStorage. */
export function AccessCode({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState(getAccessCode());

  return (
    <form
      aria-label="Access code"
      className="flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        setAccessCode(value.trim());
        onSaved();
      }}
    >
      <label className="flex flex-col gap-1 text-xs text-muted">
        Access code (needed to run investigations and approve actions)
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-64 rounded border border-border bg-surface px-2 py-1.5 text-sm text-foreground"
        />
      </label>
      <button type="submit" className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg">
        Save
      </button>
      <p className="basis-full text-xs text-muted">Stored only in this browser tab. Reading incidents needs no code.</p>
    </form>
  );
}
