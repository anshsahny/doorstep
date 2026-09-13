// One incident, polled once for every screen. Polling is cheap on purpose (docs/COST.md): every
// 2 s while anything is moving, every 10 s once the doors have settled, never while the tab is
// hidden, and it backs off when the API says it is busy.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, readIncident } from "./api";
import { loadSession, saveSession } from "./session";
import type { DrillSession } from "./session";
import type { CaseView, DecisionView, EventView, IncidentView, Resident, Snapshot, Volunteer } from "./types";

export interface IncidentState {
  session: DrillSession | null;
  recorded: boolean;
  incident: IncidentView | null;
  cases: CaseView[];
  decisions: DecisionView[];
  events: EventView[];
  residents: Resident[];
  volunteers: Volunteer[];
  error: string;
  expired: boolean;
  loading: boolean;
  lastUpdated: number;
  /** Wall-clock "now" for timers: the real time, or the moment a recorded drill was saved. */
  clockNow: () => number;
  setSession: (s: DrillSession | null) => void;
  useRecorded: (on: boolean) => void;
  refresh: () => void;
}

const Ctx = createContext<IncidentState | null>(null);

const ACTIVE_MS = 2000;
const QUIET_MS = 10000;
const BUSY_MS = 6000;

function isActive(cases: CaseView[], decisions: DecisionView[]): boolean {
  if (cases.length === 0) return true;
  return (
    cases.some((c) => ["QUEUED", "CALLING", "NO_ANSWER", "UNCLEAR", "URGENT", "NEEDS_HELP"].includes(c.state)) ||
    decisions.some((d) => d.status === "pending" || (d.status === "answered" && !d.applied_at))
  );
}

export function IncidentProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<DrillSession | null>(() => loadSession());
  const [recorded, setRecorded] = useState(false);
  const [incident, setIncident] = useState<IncidentView | null>(null);
  const [cases, setCases] = useState<CaseView[]>([]);
  const [decisions, setDecisions] = useState<DecisionView[]>([]);
  const [events, setEvents] = useState<EventView[]>([]);
  const [residents, setResidents] = useState<Resident[]>([]);
  const [volunteers, setVolunteers] = useState<Volunteer[]>([]);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(0);
  const [recordedAt, setRecordedAt] = useState(0);
  const lastSeq = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const kick = useRef<() => void>(() => {});

  const reset = useCallback(() => {
    lastSeq.current = 0;
    setIncident(null);
    setCases([]);
    setDecisions([]);
    setEvents([]);
    setResidents([]);
    setVolunteers([]);
    setError("");
    setExpired(false);
  }, []);

  const apply = useCallback((snap: Snapshot, replace: boolean) => {
    setIncident(snap.incident);
    setCases(snap.cases);
    setDecisions(snap.decisions);
    if (snap.residents) setResidents(snap.residents);
    if (snap.volunteers) setVolunteers(snap.volunteers);
    setEvents((prev) => {
      if (replace) return snap.events;
      const seen = new Set(prev.map((e) => e.seq));
      return [...prev, ...snap.events.filter((e) => !seen.has(e.seq))];
    });
    if (snap.last_seq) lastSeq.current = Math.max(lastSeq.current, snap.last_seq);
    setLastUpdated(Date.now());
    setError("");
  }, []);

  const setSession = useCallback(
    (s: DrillSession | null) => {
      saveSession(s);
      reset();
      setRecorded(false);
      setSessionState(s);
    },
    [reset],
  );

  const useRecordedDrill = useCallback(
    (on: boolean) => {
      reset();
      setRecorded(on);
    },
    [reset],
  );

  // The recorded drill: a snapshot of a real sandbox run, served as a static file. $0 to view.
  useEffect(() => {
    if (!recorded) return;
    let cancelled = false;
    setLoading(true);
    fetch("/recorded-drill.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("missing"))))
      .then((snap: Snapshot) => {
        if (cancelled) return;
        apply(snap, true);
        setRecordedAt(new Date(snap.server_time).getTime() || Date.now());
      })
      .catch(() => !cancelled && setError("The recorded drill could not be loaded."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [recorded, apply]);

  useEffect(() => {
    if (recorded || !session) return;
    let stopped = false;
    let delay = ACTIVE_MS;

    const tick = async () => {
      window.clearTimeout(timer.current);
      if (stopped) return;
      if (document.hidden) {
        timer.current = window.setTimeout(tick, QUIET_MS);
        return;
      }
      try {
        if (lastSeq.current === 0) setLoading(true);
        const snap = await readIncident(session.incidentId, session.token, lastSeq.current);
        if (stopped) return;
        apply(snap, false);
        delay = isActive(snap.cases, snap.decisions) ? ACTIVE_MS : QUIET_MS;
      } catch (err) {
        if (stopped) return;
        if (err instanceof ApiError && err.status === 401) {
          setExpired(true);
          setError(err.message);
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          // The drill's first rows take a moment to appear.
          delay = ACTIVE_MS;
        } else {
          setError(err instanceof Error ? err.message : "Could not load the drill.");
          delay = err instanceof ApiError && err.status === 429 ? BUSY_MS : QUIET_MS;
        }
      } finally {
        if (!stopped) setLoading(false);
      }
      timer.current = window.setTimeout(tick, delay);
    };

    kick.current = () => void tick();
    const onVisible = () => !document.hidden && void tick();
    document.addEventListener("visibilitychange", onVisible);
    void tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [session, recorded, apply]);

  const refresh = useCallback(() => kick.current(), []);
  const clockNow = useCallback(() => (recorded && recordedAt ? recordedAt : Date.now()), [recorded, recordedAt]);

  const value = useMemo<IncidentState>(
    () => ({
      session,
      recorded,
      incident,
      cases,
      decisions,
      events,
      residents,
      volunteers,
      error,
      expired,
      loading,
      lastUpdated,
      clockNow,
      setSession,
      useRecorded: useRecordedDrill,
      refresh,
    }),
    [session, recorded, incident, cases, decisions, events, residents, volunteers, error, expired, loading, lastUpdated, clockNow, setSession, useRecordedDrill, refresh],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useIncident(): IncidentState {
  const value = useContext(Ctx);
  if (!value) throw new Error("useIncident needs an IncidentProvider");
  return value;
}
