// Per-tab session: a sandbox drill's token lives in sessionStorage, so every tab (and every
// incognito window) is its own visitor with its own drill. Storage can be unavailable; the app
// then keeps the session in memory for this page load.

export interface DrillSession {
  incidentId: string;
  token: string;
  voiceResident: string;
  scope: "sandbox" | "captain";
  startedAt: number;
}

const KEY = "doorstep.session";
const CAPTAIN = "doorstep.captain";
let memory: DrillSession | null = null;
let captainMemory: string | null = null;

function read<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or blocked storage: memory only */
  }
}

export function loadSession(): DrillSession | null {
  return read<DrillSession>(KEY) ?? memory;
}

export function saveSession(session: DrillSession | null): void {
  memory = session;
  write(KEY, session);
}

export function captainToken(): string | null {
  return read<string>(CAPTAIN) ?? captainMemory;
}

export function saveCaptainToken(token: string | null): void {
  captainMemory = token;
  write(CAPTAIN, token);
}
