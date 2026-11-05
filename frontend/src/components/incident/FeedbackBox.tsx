"use client";

import { useState } from "react";
import type { Busy } from "@/lib/use-workspace";
import type { Investigation } from "@/lib/types";
import { Icon, Spinner } from "../ui";

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
      <section aria-label="Feedback" className="card enter flex items-center gap-3 p-4 text-sm">
        <Icon name="check" className="pop h-5 w-5 shrink-0 text-ok" />
        <p role="status">
          <span className="font-medium">Thanks, feedback saved.</span>{" "}
          <span className="text-muted">You said this was {sent.helpful ? "helpful" : "not helpful"}.</span>
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Feedback" className="card p-5 text-sm">
      <p className="font-medium">Was this answer useful?</p>
      <p className="mt-0.5 text-xs text-muted">One click helps the team see where the agent gets it wrong.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {([true, false] as const).map((v) => (
          <button
            key={String(v)}
            type="button"
            onClick={() => setVerdict(v)}
            aria-pressed={verdict === v}
            className={`btn ${verdict === v ? "btn-primary" : "btn-secondary"}`}
          >
            {v ? "Yes, helpful" : "No, not helpful"}
          </button>
        ))}
      </div>
      {verdict !== null && (
        <div className="enter mt-3 space-y-2">
          <label className="block text-xs text-muted" htmlFor="fb-note">
            What was right or wrong? (optional, kept for the team, never shown to the agent)
          </label>
          <textarea
            id="fb-note"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={500}
            rows={2}
            className="field"
          />
          <button type="button" disabled={sending} onClick={() => onSend(verdict, comment)} className="btn btn-primary">
            {sending && <Spinner />}
            {sending ? "Saving…" : "Send feedback"}
          </button>
        </div>
      )}
    </section>
  );
}
