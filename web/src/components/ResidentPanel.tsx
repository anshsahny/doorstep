// One resident: what the call found, why, and every step the agent and the policies took.

import type { Door } from "./DoorGrid";
import { ToneIcon } from "./Icons";
import {
  actorWords,
  meaningful,
  needLabel,
  redFlagLabel,
  riskLabel,
  toolWords,
  whereLabel,
} from "../lib/status";
import type { CaseView, EventView, Volunteer } from "../lib/types";

export function clock(at: string | null | undefined): string {
  if (!at) return "";
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

const STATE_WORDS: Record<string, string> = {
  QUEUED: "in the queue",
  CALLING: "calling",
  OK: "OK",
  NEEDS_HELP: "needs help",
  URGENT: "urgent",
  NO_ANSWER: "no answer",
  UNCLEAR: "not sure yet",
  ASSIGNED: "volunteer asked",
  ESCALATED: "with the captain",
  RESOLVED: "closed",
};

interface Step {
  at: string;
  who: string;
  what: string;
  why: string;
  tone: "deny" | "allow" | "human" | "plain";
}

function steps(c: CaseView | undefined, events: EventView[], volunteers: Volunteer[]): Step[] {
  const out: Step[] = [];
  for (const h of c?.history ?? []) {
    out.push({
      at: h.at,
      who: "Doorstep",
      what: `${STATE_WORDS[h.from_state] ?? h.from_state} → ${STATE_WORDS[h.to_state] ?? h.to_state}`,
      why: h.reason,
      tone: "plain",
    });
  }
  for (const e of events.filter(meaningful)) {
    const human = e.actor.startsWith("captain:") || e.actor.startsWith("volunteer:");
    if (e.type === "policy" && e.policy_decision === "deny") {
      out.push({
        at: e.at,
        who: e.by === "code" ? "Code check" : "Cedar policy",
        what: `Refused: ${toolWords(e.tool) || "an action"}`,
        why: e.reason,
        tone: "deny",
      });
    } else if (e.type === "tool_call") {
      out.push({
        at: e.at,
        who: actorWords(e.actor, volunteers),
        what: toolWords(e.tool),
        why: [e.policy_decision === "allow" ? "Allowed by policy." : "", e.rationale].filter(Boolean).join(" "),
        tone: e.policy_decision === "allow" ? "allow" : "plain",
      });
    } else if (e.type === "decision" && human) {
      out.push({ at: e.at, who: actorWords(e.actor, volunteers), what: e.reason.replace(/^dec-\d+ \[[^\]]+\] -> /, "Chose "), why: e.source === "web" ? "On the dashboard" : e.source === "telegram" ? "On Telegram" : "", tone: "human" });
    } else if (["classification", "backstop", "checkin", "decision"].includes(e.type)) {
      out.push({ at: e.at, who: actorWords(e.actor, volunteers), what: e.type === "backstop" ? "Safety backstop" : e.type === "decision" ? "Asked a person" : e.type === "checkin" ? "Check-in" : "Classified the call", why: e.reason || e.rationale, tone: "plain" });
    }
  }
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

export function ResidentPanel({
  door,
  kase,
  events,
  volunteers,
  onClose,
}: {
  door: Door;
  kase: CaseView | undefined;
  events: EventView[];
  volunteers: Volunteer[];
  onClose: () => void;
}) {
  const { resident, status } = door;
  const result = kase?.result;
  const attempt = kase?.attempt_log.at(-1);
  const trail = steps(kase, events.filter((e) => e.resident_id === resident.id), volunteers);
  const facts = [
    resident.age_band && `Age ${resident.age_band}`,
    resident.lives_alone && "Lives alone",
    resident.language === "es" && "Speaks Spanish",
  ].filter(Boolean) as string[];

  return (
    <section aria-labelledby="resident-heading" className="rounded-md border-2 border-ink bg-porch">
      <header className={`door-${status.tone} flex items-start gap-3 rounded-t p-4`} style={{ outlineOffset: "-3px" }}>
        <div className="flex-1">
          <h2 id="resident-heading" tabIndex={-1} className="text-xl font-bold">
            {resident.first_name}
          </h2>
          <p className="text-base">{whereLabel(resident)} · fictional</p>
          <p className="mt-1 text-lg">
            <span className="inline-flex items-center gap-2 font-bold">
              <ToneIcon tone={status.tone} /> {status.label}
            </span>{" "}
            · {status.detail}
          </p>
        </div>
        <button type="button" onClick={onClose} className="min-h-12 min-w-12 rounded border-2 border-current bg-porch px-3 font-bold text-ink">
          Close
        </button>
      </header>

      <div className="flex flex-col gap-4 p-4">
        {result && (
          <div>
            <h3 className="font-bold">What the call found</h3>
            {result.summary && <p>{result.summary}</p>}
            {result.key_quote && (
              <blockquote className="mt-2 border-l-4 border-line pl-3 italic">“{result.key_quote}”</blockquote>
            )}
            {result.red_flags.length > 0 && (
              <p className="mt-2">
                <strong>Warning signs:</strong> {result.red_flags.map(redFlagLabel).join(", ")}
                {result.flagged_mid_call && " (flagged during the call)"}
              </p>
            )}
            {result.backstop?.raised && (
              <p className="mt-1">
                <strong>Safety backstop:</strong> the model said {result.backstop.model_status.toLowerCase().replace("_", " ")}, but the
                resident's own words matched a warning sign, so Doorstep treated it as {result.backstop.final_status.toLowerCase()}.
              </p>
            )}
            {result.needs.length > 0 && (
              <p className="mt-1">
                <strong>Needs:</strong> {result.needs.map(needLabel).join(", ")}
              </p>
            )}
          </div>
        )}

        <div>
          <h3 className="font-bold">Why they were on the list</h3>
          <p>
            {kase ? `Call wave ${kase.risk.wave}, ${kase.risk.points} risk points: ` : ""}
            {(kase?.risk.factors ?? []).map(riskLabel).join(", ") || facts.join(", ")}
          </p>
          {resident.notes.length > 0 && <p className="text-muted">Note on file: {resident.notes.join(" ")}</p>}
        </div>

        {trail.length > 0 && (
          <div>
            <h3 className="font-bold">What happened, step by step</h3>
            <ol className="mt-2 flex flex-col gap-2">
              {trail.map((s, i) => (
                <li
                  key={i}
                  className={`rounded border-l-4 py-1 pl-3 ${
                    s.tone === "deny" ? "border-urgent bg-urgent-bg" : s.tone === "human" ? "border-calling bg-calling-bg" : s.tone === "allow" ? "border-ok" : "border-faint"
                  }`}
                >
                  <p className="text-sm text-muted">
                    <span className="num">{clock(s.at)}</span> · {s.who}
                  </p>
                  <p className="font-bold">{s.what}</p>
                  {s.why && <p className="text-sm">{s.why}</p>}
                </li>
              ))}
            </ol>
          </div>
        )}

        {attempt && attempt.transcript.length > 0 && (
          <details className="rounded border-2 border-faint p-2">
            <summary className="min-h-10 cursor-pointer font-bold">
              Transcript ({attempt.channel === "simulated" ? "simulated resident" : attempt.channel}, attempt {attempt.attempt})
            </summary>
            <ol className="mt-2 flex flex-col gap-1">
              {attempt.transcript.map((t, i) => (
                <li key={i}>
                  <strong>{t.speaker === "agent" ? "Doorstep" : resident.first_name}:</strong> {t.text}
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </section>
  );
}
