// What each door says. One place turns a case into a door state, a word, an icon and a detail
// line, so colour never carries meaning alone and every screen uses the same words.

import profile from "../generated/profile.json";
import type { CaseView, DecisionView, EventView, Resident, Volunteer } from "./types";

export type Tone = "not_called" | "calling" | "your_call" | "ok" | "needs_help" | "no_answer" | "urgent";

export interface DoorStatus {
  tone: Tone;
  label: string;
  detail: string;
  /** For the filter strip and the report: the group a door counts in. */
  group: "ok" | "needs_help" | "urgent" | "waiting";
}

const labels = profile as {
  display_name: string;
  risk_factors: Record<string, string>;
  red_flags: Record<string, string>;
  needs: Record<string, string>;
};

export const hazardName = labels.display_name;
export const needLabel = (id: string) => labels.needs[id] ?? id.replace(/_/g, " ");
export const redFlagLabel = (id: string) => labels.red_flags[id] ?? id.replace(/_/g, " ");
export const riskLabel = (id: string) => labels.risk_factors[id] ?? id.replace(/_/g, " ");

/** Agent-written outcomes can be ids ("captain_handling"); show them as words. */
export function words(text: string | null | undefined): string {
  if (!text) return "";
  const t = text.replace(/_/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function everUrgent(c: CaseView): boolean {
  return (
    c.result?.status === "URGENT" ||
    c.history.some((h) => h.to_state === "URGENT")
  );
}

function firstName(volunteers: Volunteer[], id: string | null): string {
  const v = volunteers.find((x) => x.id === id);
  return v ? v.name.split(" ")[0] : "A volunteer";
}

export function doorStatus(
  c: CaseView | undefined,
  opts: { voice: boolean; volunteers: Volunteer[]; maxAttempts?: number },
): DoorStatus {
  const max = opts.maxAttempts ?? 3;
  if (!c || c.state === "QUEUED") {
    return opts.voice
      ? { tone: "your_call", label: "Waiting for you", detail: "Answer this call as them", group: "waiting" }
      : { tone: "not_called", label: "Not called yet", detail: "In the call queue", group: "waiting" };
  }
  if (c.state === "CALLING") {
    return opts.voice
      ? { tone: "your_call", label: "On the call", detail: "You are answering as them", group: "waiting" }
      : { tone: "calling", label: "Calling…", detail: `Attempt ${Math.max(c.attempts, 1)} of ${max}`, group: "waiting" };
  }
  const handled = c.state === "RESOLVED";
  if (everUrgent(c)) {
    const flags = (c.result?.red_flags ?? []).map(redFlagLabel);
    return {
      tone: "urgent",
      label: "Urgent",
      detail: handled
        ? `Captain handled it${c.outcome && !/captain/i.test(c.outcome) ? `: ${words(c.outcome)}` : ""}`
        : c.assigned_volunteer
          ? `${firstName(opts.volunteers, c.assigned_volunteer)} is going`
          : flags.length
            ? `Captain paged · ${flags[0]}`
            : "Captain paged",
      group: "urgent",
    };
  }
  if (c.state === "NO_ANSWER") {
    return { tone: "no_answer", label: "No answer", detail: `Tried ${c.attempts} of ${max} · calling back`, group: "waiting" };
  }
  if (c.state === "UNCLEAR") {
    return { tone: "no_answer", label: "Not sure yet", detail: "Calling back to check", group: "waiting" };
  }
  if (c.state === "ESCALATED" && c.result?.status === "NO_ANSWER") {
    return { tone: "no_answer", label: "No answer", detail: `${c.attempts} tries · captain asked`, group: "needs_help" };
  }
  const needs = (c.result?.needs ?? []).map(needLabel);
  if (c.state === "ASSIGNED") {
    return {
      tone: "needs_help",
      label: "Needs help",
      detail: `${firstName(opts.volunteers, c.assigned_volunteer)} is going`,
      group: "needs_help",
    };
  }
  if (c.state === "NEEDS_HELP" || c.state === "ESCALATED" || c.result?.status === "NEEDS_HELP") {
    return {
      tone: "needs_help",
      label: handled ? "Helped" : "Needs help",
      detail: handled ? words(c.outcome) || needs[0] || "Followed up" : needs[0] || "Waiting on the captain",
      group: "needs_help",
    };
  }
  if (c.result?.status === "NO_ANSWER") {
    return { tone: "no_answer", label: "No answer", detail: words(c.outcome) || "Followed up", group: "needs_help" };
  }
  return { tone: "ok", label: "OK", detail: "Checked in", group: "ok" };
}

export function place(r: Resident): { building: string; unit: string | null; street: string } {
  const street = r.building.replace(/\s*\(.*\)\s*$/, "");
  return { building: r.building, unit: r.unit, street };
}

export function whereLabel(r: Resident): string {
  return r.unit ? `${r.building}, unit ${r.unit}` : r.building;
}

// --- decisions -------------------------------------------------------------------------------

export function decisionTitle(d: DecisionView, residents: Resident[], volunteers: Volunteer[]): string {
  const who = residents.find((r) => r.id === d.resident_id)?.first_name ?? "A resident";
  switch (d.name) {
    case "doorstep-urgent-red-flag":
      return `${who} may be in danger`;
    case "doorstep-approve-door-knock":
      return `Send someone to ${who}'s door?`;
    case "doorstep-high-risk-no-answer":
      return `${who} hasn't answered`;
    case "doorstep-unmet-need":
      return `${who} needs something the team can't arrange`;
    case "doorstep-volunteer-update":
      return `${firstName(volunteers, d.audience)}'s visit to ${who}`;
    case "doorstep-borderline-activation":
      return "Start check-ins for this alert?";
    default:
      return `A decision about ${who}`;
  }
}

export function responderLabel(responder: string | null, volunteers: Volunteer[], source?: string | null): string {
  if (!responder) return "";
  if (responder === "captain:auto-approve") return "the simulated captain";
  const id = responder.split(":")[1];
  const name = volunteers.find((v) => v.id === id)?.name ?? id;
  const via = source === "web" ? " on the dashboard" : source === "telegram" ? " on Telegram" : "";
  return `${name}${via}`;
}

// --- the audit trail in plain words ------------------------------------------------------------

const TOOL_WORDS: Record<string, string> = {
  get_org_profile: "Read the team's profile",
  get_roster: "Read the roster",
  score_residents: "Scored who to call first",
  get_resident_memory: "Read standing notes",
  find_relief_centres: "Looked up relief centres",
  find_nearest_volunteers: "Found the nearest volunteers",
  schedule_recheck: "Set a reminder to check again",
  send_resident_tip: "Sent a safety tip",
  escalate_to_captain: "Paged the captain",
  close_case: "Closed the case",
  assign_volunteer: "Asked a volunteer to visit",
  broadcast_to_volunteers: "Messaged the volunteer group",
  notify_family: "Contacted family",
  place_checkin_call: "Placed a real call",
  start_simulated_checkin: "Started a practice call",
  record_emergency_call: "Recorded an emergency call",
  record_answer: "Noted an answer",
  flag_urgent: "Flagged urgent during the call",
  end_call: "Ended the call",
};

export function toolWords(tool: string | null): string {
  if (!tool) return "";
  return TOOL_WORDS[tool] ?? tool.replace(/_/g, " ");
}

export function actorWords(actor: string, volunteers: Volunteer[]): string {
  const [kind, id] = actor.split(":");
  if (kind === "agent") return `Agent (${id.replace(/_/g, " ")})`;
  if (kind === "system") return "Doorstep";
  if (kind === "captain" || kind === "volunteer") {
    if (id === "auto-approve") return "Simulated captain";
    return volunteers.find((v) => v.id === id)?.name ?? id;
  }
  return actor;
}

/** Events worth showing a person. Tool "attempted" rows and structured-output bookkeeping are not. */
export function meaningful(e: EventView): boolean {
  if (e.type === "tool_call") {
    if (e.reason === "attempted") return false;
    if (e.tool && /^[A-Z]/.test(e.tool)) return false;
    if (e.tool && ["get_org_profile", "get_roster", "get_resident_memory", "record_answer"].includes(e.tool)) return false;
  }
  return true;
}
