// A decision waiting on a person. The same options, and the same single path
// (`respond_to_decision`), as the buttons on the captain's Telegram.

import { useEffect, useState } from "react";
import { ApiError, answerDecision } from "../lib/api";
import { useIncident } from "../lib/incident";
import { decisionTitle, responderLabel, whereLabel } from "../lib/status";
import type { DecisionView } from "../lib/types";
import { clock } from "./ResidentPanel";

function minutesLeft(at: string | null, now: number): string {
  if (!at) return "";
  const ms = new Date(at).getTime() - now;
  if (ms <= 0) return "expiring now";
  const m = Math.round(ms / 60000);
  return m < 1 ? "expires in under a minute" : `expires in ${m} min`;
}

function waited(at: string, now: number): string {
  const s = Math.max(0, Math.round((now - new Date(at).getTime()) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

export function DecisionCard({ decision }: { decision: DecisionView }) {
  const { session, recorded, residents, volunteers, events, refresh, clockNow } = useIncident();
  const [sending, setSending] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(() => clockNow());
  useEffect(() => {
    setNow(clockNow());
    if (decision.status !== "pending" || recorded) return;
    const t = window.setInterval(() => setNow(clockNow()), 1000);
    return () => window.clearInterval(t);
  }, [decision.status, recorded, clockNow]);

  const resident = residents.find((r) => r.id === decision.resident_id);
  const title = decisionTitle(decision, residents, volunteers);
  const forVolunteer = decision.name === "doorstep-volunteer-update";
  const headingId = `decision-${decision.id}`;

  const choose = async (optionId: string) => {
    if (!session || recorded) return;
    setSending(optionId);
    setMessage("");
    try {
      const result = await answerDecision(session.incidentId, decision.id, optionId, session.token);
      setMessage(result.stale ? "Someone already answered this one. Nothing was sent twice." : "Sent. Doorstep is carrying it out…");
      refresh();
    } catch (err) {
      setSending(null);
      setMessage(err instanceof ApiError ? err.message : "Your answer did not go through. Please try again.");
    }
  };

  if (decision.status !== "pending") {
    const chosen = decision.options.find((o) => o.id === decision.response)?.label ?? decision.response;
    const source = events.find(
      (e) => e.type === "decision" && e.actor === decision.responder && e.reason.startsWith(`${decision.id} `),
    )?.source;
    return (
      <li className="rounded-md border-2 border-faint bg-porch p-3">
        <p className="font-bold">{title}</p>
        <p className="text-base">
          {decision.status === "answered" ? (
            <>
              <strong>“{chosen}”</strong> · {responderLabel(decision.responder, volunteers, source)} at{" "}
              <span className="num">{clock(decision.responded_at)}</span>
              {!decision.applied_at && " · carrying it out…"}
            </>
          ) : (
            "Nobody answered in time. Doorstep followed its safe default and kept the case open."
          )}
        </p>
      </li>
    );
  }

  return (
    <li>
      <article
        aria-labelledby={headingId}
        className={`rounded-md border-4 bg-porch p-4 ${decision.name === "doorstep-urgent-red-flag" ? "border-urgent" : "border-check"}`}
      >
        <h3 id={headingId} className="text-lg font-bold">
          {title}
        </h3>
        <p className="text-sm text-muted">
          {resident ? `${whereLabel(resident)} · ` : ""}waiting <span className="num">{waited(decision.created_at, now)}</span> ·{" "}
          {minutesLeft(decision.expires_at, now)}
          {forVolunteer && ` · for ${volunteers.find((v) => v.id === decision.audience)?.name ?? "the volunteer"}`}
        </p>
        <p className="mt-2">{decision.reason}</p>
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={`Answer: ${title}`}>
          {decision.options.map((o, i) => (
            <button
              key={o.id}
              type="button"
              disabled={sending !== null || recorded}
              onClick={() => choose(o.id)}
              className={`min-h-14 rounded-md border-2 px-4 text-lg font-bold disabled:opacity-60 ${
                i === 0 ? "border-ink bg-ink text-porch" : "border-ink bg-porch text-ink"
              }`}
            >
              {sending === o.id ? "Sending…" : o.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-sm text-muted">
          {recorded ? "Recorded drill: these buttons are shown as they were, not live." : "The same buttons the captain gets on Telegram."}
        </p>
        <p role="status" aria-live="polite" className="mt-1 font-bold">
          {message}
        </p>
      </article>
    </li>
  );
}
