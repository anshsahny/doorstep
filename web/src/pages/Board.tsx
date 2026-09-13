import { useEffect, useMemo, useRef, useState } from "react";
import { CallPanel } from "../components/CallPanel";
import { CounterStrip, DoorGrid, DoorList, counts, useDoors } from "../components/DoorGrid";
import type { Group } from "../components/DoorGrid";
import { ResidentPanel } from "../components/ResidentPanel";
import { StartDrill } from "../components/StartDrill";
import { useIncident } from "../lib/incident";
import { Link } from "../lib/router";
import { decisionTitle, hazardName, whereLabel } from "../lib/status";

function elapsed(startedAt: string | undefined, now: number): string {
  if (!startedAt) return "";
  const s = Math.max(0, Math.round((now - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function Board() {
  const inc = useIncident();
  const { session, recorded, incident, cases, decisions, events, residents, volunteers } = inc;
  const [filter, setFilter] = useState<Group | null>(null);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const panelRef = useRef<HTMLDivElement>(null);
  const doors = useDoors(residents, cases, volunteers, incident?.voice_residents ?? []);
  const pending = decisions.filter((d) => d.status === "pending");

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // Announce what changed, without reading the whole board: urgent doors at once, the rest as a
  // count sentence when it changes.
  const n = counts(doors);
  const summary = doors.length
    ? `${n.urgent} urgent, ${n.needs_help} need help, ${n.ok} OK, ${n.waiting} still to reach.`
    : "";
  const urgentNames = doors.filter((d) => d.status.tone === "urgent").map((d) => d.resident.first_name);
  const urgentKey = urgentNames.join(",");
  const [urgentAnnouncement, setUrgentAnnouncement] = useState("");
  const announced = useRef<Set<string>>(new Set());
  useEffect(() => {
    const fresh = urgentNames.filter((x) => !announced.current.has(x));
    fresh.forEach((x) => announced.current.add(x));
    if (fresh.length) setUrgentAnnouncement(`Urgent: ${fresh.join(" and ")}. The captain has been paged.`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urgentKey]);

  const selectedDoor = doors.find((d) => d.resident.id === selected);
  useEffect(() => {
    if (selectedDoor) panelRef.current?.querySelector<HTMLElement>("#resident-heading")?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const voiceResident = useMemo(
    () => residents.find((r) => r.id === (incident?.voice_residents ?? [])[0]),
    [residents, incident],
  );
  const voiceCase = cases.find((c) => c.resident_id === voiceResident?.id);
  const voiceAnswered = Boolean(voiceCase && voiceCase.state !== "QUEUED" && voiceCase.state !== "CALLING");

  if (!session && !recorded) {
    return (
      <div className="flex max-w-2xl flex-col gap-4">
        <h1 tabIndex={-1} className="text-2xl font-bold">
          No drill running yet
        </h1>
        <p>
          Start your own drill: a real, archived National Weather Service warning arrives, Doorstep phones twelve fictional neighbours, and their doors fill
          in here as each call ends. Nobody real is contacted.
        </p>
        <StartDrill />
        <p>
          Or{" "}
          <button type="button" className="min-h-12 font-bold underline" onClick={() => inc.useRecorded(true)}>
            watch a recorded drill
          </button>
          .
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="sr-only" aria-live="polite">
        {summary}
      </div>
      <div className="sr-only" aria-live="assertive">
        {urgentAnnouncement}
      </div>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-base font-bold text-urgent">
            {incident?.alert.event || `${hazardName} alert`}
            {incident?.alert.severity ? ` · ${incident.alert.severity}` : ""}
          </p>
          <h1 tabIndex={-1} className="text-2xl font-bold">
            {recorded ? "A recorded drill" : "Your drill"}: Juniper Court
          </h1>
          <p className="text-base text-muted">
            {incident ? (
              <>
                {recorded ? "Ran for" : "Running for"} <span className="num">{elapsed(incident.started_at, recorded ? inc.clockNow() : now)}</span> ·{" "}
                {incident.mode === "sandbox" || recorded ? "Sandbox: no real calls or messages" : incident.mode}
              </>
            ) : inc.loading || !inc.error ? (
              "Starting: reading the alert and deciding who to call first…"
            ) : null}
          </p>
        </div>
        {recorded && (
          <div className="flex items-center gap-3">
            <StartDrill size="small" />
          </div>
        )}
      </div>

      {inc.error && (
        <div role="alert" className="rounded-md border-2 border-check bg-check-bg p-3">
          <p className="font-bold">{inc.error}</p>
          {inc.expired && <StartDrill size="small" />}
        </div>
      )}

      {pending.length > 0 && (
        <Link
          to="/decisions"
          className="flex min-h-14 flex-wrap items-center justify-between gap-2 rounded-md border-4 border-urgent bg-urgent-bg px-4 py-2 text-ink no-underline"
        >
          <span className="text-lg font-bold">
            <span className="num">{pending.length}</span> {pending.length === 1 ? "decision is" : "decisions are"} waiting for you
          </span>
          <span className="text-base">
            {decisionTitle(pending[0], residents, volunteers)} · <strong className="underline">Review</strong>
          </span>
        </Link>
      )}

      {voiceResident && session && !recorded && (
        <CallPanel resident={voiceResident} incidentId={session.incidentId} token={session.token} answered={voiceAnswered} />
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <section aria-labelledby="doors-heading" className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="doors-heading" className="text-xl font-bold">
              Doors
            </h2>
            <div role="group" aria-label="Board layout" className="flex gap-1">
              {(["grid", "list"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  aria-pressed={view === v}
                  onClick={() => setView(v)}
                  className={`min-h-12 rounded-md border-2 border-ink px-3 font-bold ${view === v ? "bg-ink text-porch" : "bg-porch"}`}
                >
                  {v === "grid" ? "Door grid" : "List"}
                </button>
              ))}
            </div>
          </div>
          {doors.length > 0 && <CounterStrip doors={doors} filter={filter} onFilter={setFilter} />}
          {doors.length === 0 ? (
            <p className="rounded-md border-2 border-dashed border-line p-6 text-lg">Loading the twelve doors…</p>
          ) : view === "grid" ? (
            <DoorGrid doors={doors} selected={selected} filter={filter} onSelect={(id) => setSelected(id === selected ? null : id)} />
          ) : (
            <DoorList doors={doors} filter={filter} onSelect={setSelected} />
          )}
        </section>

        <div ref={panelRef} className="min-w-0 lg:sticky lg:top-4 lg:self-start">
          {selectedDoor ? (
            <ResidentPanel
              door={selectedDoor}
              kase={cases.find((c) => c.resident_id === selectedDoor.resident.id)}
              events={events}
              volunteers={volunteers}
              onClose={() => {
                const id = selected;
                setSelected(null);
                requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-resident="${id}"]`)?.focus());
              }}
            />
          ) : (
            <aside className="rounded-md border-2 border-dashed border-line p-4">
              <h2 className="text-lg font-bold">Pick a door</h2>
              <p>
                Choose any door to see what the call found, why that resident was called when they were, and each step the agent and
                the policies took.
              </p>
              {doors.some((d) => d.status.tone === "urgent") && (
                <p className="mt-2">
                  Start with{" "}
                  {doors
                    .filter((d) => d.status.tone === "urgent")
                    .map((d) => (
                      <button
                        key={d.resident.id}
                        type="button"
                        className="mr-2 min-h-12 font-bold underline"
                        onClick={() => setSelected(d.resident.id)}
                      >
                        {d.resident.first_name} ({whereLabel(d.resident)})
                      </button>
                    ))}
                </p>
              )}
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
