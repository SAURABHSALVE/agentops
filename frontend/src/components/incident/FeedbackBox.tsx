"use client";

import { useState } from "react";
import type { Busy } from "@/lib/use-workspace";
import type { Investigation } from "@/lib/types";

interface Props {
  investigation: Investigation | null;
  busy: Busy;
  onSend: (helpful: boolean, comment: string) => void;
}

/** "Was this useful?" for a finished investigation. The note is stored for the team; it never goes to the agent. */
export function FeedbackBox({ investigation, busy, onSend }: Props) {
  const [verdict, setVerdict] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");

  if (!investigation || investigation.status === "running") return null;
  const sent = investigation.feedback;
  const sending = busy === "feedback";

  if (sent) {
    return (
      <section aria-label="Feedback" className="rounded-lg border border-border bg-surface p-4 text-sm">
        <p role="status">
          <span className="font-medium">Thanks, feedback saved.</span>{" "}
          <span className="text-muted">You said this was {sent.helpful ? "helpful" : "not helpful"}.</span>
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Feedback" className="rounded-lg border border-border bg-surface p-4 text-sm">
      <p className="font-medium">Was this answer useful?</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {([true, false] as const).map((v) => (
          <button
            key={String(v)}
            type="button"
            onClick={() => setVerdict(v)}
            aria-pressed={verdict === v}
            className={`rounded border px-3 py-1.5 font-medium ${
              verdict === v ? "border-accent bg-accent text-accent-fg" : "border-border bg-surface-2 hover:border-accent"
            }`}
          >
            {v ? "Yes, helpful" : "No, not helpful"}
          </button>
        ))}
      </div>
      {verdict !== null && (
        <div className="mt-3 space-y-2">
          <label className="block text-xs text-muted" htmlFor="fb-note">
            What was right or wrong? (optional, kept for the team, never shown to the agent)
          </label>
          <textarea
            id="fb-note"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={500}
            rows={2}
            className="w-full rounded border border-border bg-surface px-2 py-1.5 text-sm"
          />
          <button
            type="button"
            disabled={sending}
            onClick={() => onSend(verdict, comment)}
            className="rounded bg-accent px-3 py-1.5 font-medium text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending ? "Saving…" : "Send feedback"}
          </button>
        </div>
      )}
    </section>
  );
}
