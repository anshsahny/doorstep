// The dashboard API client. The API URL comes from /config.json, so one build runs anywhere.

import type { DecisionView, Report, Snapshot } from "./types";

export class ApiError extends Error {
  status: number;
  recordedDrill: boolean;
  videoUrl: string;
  constructor(status: number, message: string, extra: { recorded_drill?: boolean; video_url?: string } = {}) {
    super(message);
    this.status = status;
    this.recordedDrill = Boolean(extra.recorded_drill);
    this.videoUrl = extra.video_url ?? "";
  }
}

let configPromise: Promise<{ apiUrl: string }> | null = null;

export function config(): Promise<{ apiUrl: string }> {
  configPromise ??= fetch("/config.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : { apiUrl: "" }))
    .catch(() => ({ apiUrl: "" }));
  return configPromise;
}

export async function apiUrl(): Promise<string> {
  return (await config()).apiUrl.replace(/\/$/, "");
}

async function call<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const base = await apiUrl();
  if (!base) throw new ApiError(0, "The dashboard is not connected to an API yet.");
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body) headers["content-type"] = "application/json";
  if (init.token) headers.authorization = `Bearer ${init.token}`;
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Could not reach Doorstep. Check your connection and try again.");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      body.error ??
      (res.status === 429
        ? "Doorstep is busy right now. Please wait a moment and try again."
        : `Something went wrong (${res.status}). Please try again.`);
    throw new ApiError(res.status, message, body);
  }
  return body as T;
}

export interface StartedDrill {
  incident_id: string;
  token: string;
  expires_in: number;
  voice_resident: string;
  replayed: boolean;
}

export function startDrill(idempotencyKey: string): Promise<StartedDrill> {
  return call("/drills", { method: "POST", headers: { "idempotency-key": idempotencyKey } });
}

export function captainSession(passcode: string): Promise<{ token: string; expires_in: number }> {
  return call("/captain/session", { method: "POST", body: JSON.stringify({ passcode }) });
}

export function readIncident(incidentId: string, token: string, since: number): Promise<Snapshot> {
  const query = since > 0 ? `?since=${since}` : "";
  return call(`/incidents/${encodeURIComponent(incidentId)}${query}`, { token });
}

export function readReport(incidentId: string, token: string): Promise<Report> {
  return call(`/incidents/${encodeURIComponent(incidentId)}?view=report`, { token });
}

export async function answerDecision(
  incidentId: string,
  decisionId: string,
  optionId: string,
  token: string,
): Promise<{ accepted: boolean; stale?: boolean }> {
  try {
    return await call(
      `/incidents/${encodeURIComponent(incidentId)}/decisions/${encodeURIComponent(decisionId)}`,
      { method: "POST", token, body: JSON.stringify({ option_id: optionId }) },
    );
  } catch (err) {
    // A stale screen: the decision was answered elsewhere (Telegram, another tab).
    if (err instanceof ApiError && err.status === 409) return { accepted: false, stale: true };
    throw err;
  }
}

export type { DecisionView };
