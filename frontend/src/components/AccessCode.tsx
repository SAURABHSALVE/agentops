"use client";

import { useState } from "react";
import { getAccessCode, setAccessCode } from "@/lib/api";
import { Icon } from "./ui";

/** Shown only when the deployment protects writes with an access code. The code is kept in sessionStorage. */
export function AccessCode({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState(getAccessCode());

  return (
    <form
      aria-label="Access code"
      className="card flex flex-wrap items-end gap-3 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setAccessCode(value.trim());
        onSaved();
      }}
    >
      <label className="flex min-w-[16rem] flex-1 flex-col gap-1 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <Icon name="lock" className="h-3.5 w-3.5" />
          Access code (needed to run investigations and approve actions)
        </span>
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="field"
        />
      </label>
      <button type="submit" className="btn btn-primary">
        Save
      </button>
      <p className="basis-full text-xs text-muted">Stored only in this browser tab. Reading incidents needs no code.</p>
    </form>
  );
}
