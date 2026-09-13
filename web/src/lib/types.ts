// Shapes returned by the dashboard API (api/doorstep_api/snapshot.py). All data is fictional.

export type CaseState =
  | "QUEUED"
  | "CALLING"
  | "OK"
  | "NEEDS_HELP"
  | "URGENT"
  | "NO_ANSWER"
  | "UNCLEAR"
  | "ASSIGNED"
  | "ESCALATED"
  | "RESOLVED";

export type CheckinStatus = "OK" | "NEEDS_HELP" | "URGENT" | "NO_ANSWER" | "UNCLEAR";

export interface Resident {
  id: string;
  first_name: string;
  unit: string | null;
  building: string;
  address_label: string;
  age_band: string;
  lives_alone: boolean;
  has_ac: boolean;
  power_dependent: boolean;
  mobility_limited: boolean;
  language: string;
  notes: string[];
  lat: number | null;
  lng: number | null;
}

export interface Volunteer {
  id: string;
  name: string;
  role: "captain" | "volunteer";
}

export interface Turn {
  speaker: "agent" | "resident";
  text: string;
}

export interface Attempt {
  attempt: number;
  channel: string;
  started_at: string | null;
  ended_at: string | null;
  answered: boolean;
  transcript: Turn[];
  answers: Record<string, string>;
}

export interface Transition {
  from_state: CaseState;
  to_state: CaseState;
  at: string;
  reason: string;
}

export interface CaseView {
  resident_id: string;
  state: CaseState;
  attempts: number;
  risk: { points: number; wave: number; factors: string[] };
  result: {
    status: CheckinStatus;
    needs: string[];
    red_flags: string[];
    summary: string;
    key_quote: string;
    flagged_mid_call: boolean;
    confidence: number;
    language: string;
    backstop: {
      matched_categories: string[];
      matched_phrases: string[];
      model_status: string;
      final_status: string;
      raised: boolean;
      disagreement: string;
    } | null;
  } | null;
  attempt_log: Attempt[];
  assigned_volunteer: string | null;
  outcome: string | null;
  updated_at: string;
  history: Transition[];
}

export interface DecisionView {
  id: string;
  resident_id: string | null;
  name: string;
  reason: string;
  options: { id: string; label: string }[];
  status: "draft" | "pending" | "answered" | "expired";
  audience: string;
  created_at: string;
  expires_at: string | null;
  responder: string | null;
  response: string | null;
  responded_at: string | null;
  applied_at: string | null;
}

export interface EventView {
  seq: number;
  at: string;
  actor: string;
  type: string;
  resident_id: string | null;
  tool: string | null;
  policy_decision: "allow" | "deny" | null;
  reason: string;
  rationale: string;
  input_summary: string;
  by: string | null;
  source: string | null;
  outcome: string | null;
}

export interface IncidentView {
  id: string;
  mode: "live" | "drill" | "sandbox";
  status: string;
  profile_id: string;
  started_at: string;
  resident_ids: string[];
  voice_residents: string[];
  alert: {
    event: string;
    severity: string;
    headline: string;
    area_desc: string;
    onset: string;
    expires: string;
    sender: string;
  };
  assessment: { severity: string | null; window: string | null; rationale: string | null };
}

export interface Snapshot {
  incident: IncidentView;
  cases: CaseView[];
  decisions: DecisionView[];
  events: EventView[];
  last_seq: number | null;
  server_time: string;
  residents?: Resident[];
  volunteers?: Volunteer[];
}

export interface Report {
  incident_id: string;
  mode: string;
  residents: number;
  reached: number;
  all_reached: boolean;
  not_reached: string[];
  outcomes: Record<string, number>;
  seconds_to_first_call: number | null;
  seconds_to_reach_everyone: number | null;
  seconds_to_last_reached: number | null;
  urgent: {
    resident_id: string;
    state: string;
    call_to_page_seconds: number | null;
    flagged_mid_call: boolean;
  }[];
  decisions: {
    raised: number;
    answered_by_people: number;
    answered_automatically: number;
    waiting: number;
    expired: number;
    by_channel: Record<string, number>;
  };
  automated_actions: number;
  policy_denials: EventView[];
  messages: Record<string, number>;
  phone_tree: { residents: number; minutes_per_call: number; volunteers_calling: number; minutes: number };
  server_time?: string;
}
