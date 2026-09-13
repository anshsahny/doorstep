// The door grid: every resident is a door, laid out the way the block is. Juniper Court's floors
// from the top down, then the houses along each street. Doors fill in as check-ins land.

import { useEffect, useMemo, useRef, useState } from "react";
import { doorStatus, place, whereLabel } from "../lib/status";
import type { DoorStatus } from "../lib/status";
import type { CaseView, Resident, Volunteer } from "../lib/types";
import { ToneIcon } from "./Icons";

export type Group = DoorStatus["group"];

export interface Door {
  resident: Resident;
  status: DoorStatus;
}

export function useDoors(
  residents: Resident[],
  cases: CaseView[],
  volunteers: Volunteer[],
  voiceResidents: string[],
): Door[] {
  return useMemo(() => {
    const byId = new Map(cases.map((c) => [c.resident_id, c]));
    return residents.map((resident) => ({
      resident,
      status: doorStatus(byId.get(resident.id), {
        voice: voiceResidents.includes(resident.id),
        volunteers,
      }),
    }));
  }, [residents, cases, volunteers, voiceResidents]);
}

export const GROUPS: { id: Group; one: string; many: string }[] = [
  { id: "urgent", one: "urgent", many: "urgent" },
  { id: "needs_help", one: "needs help", many: "need help" },
  { id: "ok", one: "OK", many: "OK" },
  { id: "waiting", one: "still to reach", many: "still to reach" },
];

export function counts(doors: Door[]): Record<Group, number> {
  const out: Record<Group, number> = { urgent: 0, needs_help: 0, ok: 0, waiting: 0 };
  for (const d of doors) out[d.status.group] += 1;
  return out;
}

/** The counter sentence doubles as the filter: press a part to see only those doors. */
export function CounterStrip({
  doors,
  filter,
  onFilter,
}: {
  doors: Door[];
  filter: Group | null;
  onFilter: (g: Group | null) => void;
}) {
  const n = counts(doors);
  return (
    <div role="group" aria-label="Filter doors by status" className="flex flex-wrap items-center gap-2">
      {GROUPS.map((g) => {
        const pressed = filter === g.id;
        const tone =
          g.id === "urgent"
            ? "border-urgent text-urgent"
            : g.id === "needs_help"
              ? "border-check text-check"
              : g.id === "ok"
                ? "border-ok text-ok"
                : "border-line text-muted";
        return (
          <button
            key={g.id}
            type="button"
            aria-pressed={pressed}
            onClick={() => onFilter(pressed ? null : g.id)}
            className={`min-h-12 rounded-full border-2 px-4 text-base font-bold ${tone} ${
              pressed ? "bg-ink !text-porch !border-ink" : "bg-porch"
            }`}
          >
            <span className="num text-lg">{n[g.id]}</span> {n[g.id] === 1 ? g.one : g.many}
          </button>
        );
      })}
      {filter && (
        <button type="button" onClick={() => onFilter(null)} className="min-h-12 px-3 text-base underline">
          Show all doors
        </button>
      )}
    </div>
  );
}

function DoorTile({
  door,
  selected,
  dimmed,
  onSelect,
}: {
  door: Door;
  selected: boolean;
  dimmed: boolean;
  onSelect: (id: string) => void;
}) {
  const { resident, status } = door;
  const wasUrgent = useRef(status.tone === "urgent");
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    if (status.tone === "urgent" && !wasUrgent.current) setFresh(true);
    wasUrgent.current = status.tone === "urgent";
  }, [status.tone]);

  const label = resident.unit ?? place(resident).building.replace(/^.*\(|\)$/g, "");
  return (
    <button
      type="button"
      className={`door door-${status.tone} ${fresh ? "door-new" : ""} ${dimmed ? "door-dimmed" : ""}`}
      aria-pressed={selected}
      onClick={() => onSelect(resident.id)}
      data-resident={resident.id}
      data-tone={status.tone}
    >
      {/* The visible words are the accessible name (WCAG 2.5.3), with hidden commas and
          full stops so a screen reader pauses between them. */}
      <span className="text-sm font-bold tracking-wide opacity-85">
        <span className="sr-only">{resident.unit ? "Unit " : ""}</span>
        {label}
        <span className="sr-only">, </span>
      </span>
      <span className="text-lg font-bold leading-tight">
        {resident.first_name}
        <span className="sr-only">: </span>
      </span>
      <span className="mt-auto flex items-center gap-1.5 text-base font-bold">
        <ToneIcon tone={status.tone} />
        {status.label}
        <span className="sr-only">. </span>
      </span>
      <span className="pr-3 text-sm leading-snug [overflow-wrap:anywhere]">{status.detail}</span>
    </button>
  );
}

export function DoorGrid({
  doors,
  selected,
  filter,
  onSelect,
}: {
  doors: Door[];
  selected: string | null;
  filter: Group | null;
  onSelect: (id: string) => void;
}) {
  const inBuilding = doors.filter((d) => d.resident.unit);
  const building = inBuilding[0]?.resident.building ?? "";
  const floors = Math.max(0, ...inBuilding.map((d) => parseInt(d.resident.unit ?? "0", 10) || 0));
  const columns = Math.max(
    0,
    ...inBuilding.map((d) => (d.resident.unit ?? "A").slice(-1).toUpperCase().charCodeAt(0) - 64),
  );
  const unitDoor = new Map(inBuilding.map((d) => [d.resident.unit!.toUpperCase(), d]));

  const streets = new Map<string, Door[]>();
  for (const d of doors.filter((x) => !x.resident.unit)) {
    const street = place(d.resident).street;
    streets.set(street, [...(streets.get(street) ?? []), d]);
  }

  const tile = (d: Door) => (
    <DoorTile
      key={d.resident.id}
      door={d}
      selected={selected === d.resident.id}
      dimmed={filter !== null && d.status.group !== filter}
      onSelect={onSelect}
    />
  );

  return (
    <div className="flex flex-col gap-6">
      {floors > 0 && (
        <section aria-label={`${building}, ${floors} floors`}>
          <h3 className="mb-2 text-lg font-bold">{building}</h3>
          <div className="@container rounded-md border-2 border-ink bg-faint/40 p-2 sm:p-3">
            {Array.from({ length: floors }, (_, i) => floors - i).map((floor) => (
              <div key={floor} className="flex items-stretch gap-2 border-b-2 border-line/40 py-2 last:border-b-0">
                <div className="flex w-8 shrink-0 items-center justify-center text-sm font-bold text-muted" aria-hidden>
                  {floor}
                </div>
                <ul
                  className="grid flex-1 grid-cols-2 gap-2 @[36rem]:[grid-template-columns:repeat(var(--cols),minmax(0,1fr))]"
                  style={{ ["--cols" as string]: columns }}
                  aria-label={`Floor ${floor}`}
                >
                  {Array.from({ length: columns }, (_, c) => {
                    const unit = `${floor}${String.fromCharCode(65 + c)}`;
                    const d = unitDoor.get(unit);
                    return (
                      <li key={unit} className={d ? "flex min-w-0 flex-col" : "hidden min-w-0 flex-col @[36rem]:flex"} aria-hidden={d ? undefined : true}>
                        {d ? tile(d) : <div className="vacant" title={`${unit}: not on the list`} />}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </section>
      )}
      {streets.size > 0 && (
        <section aria-label="Houses on the surrounding streets">
          <h3 className="mb-2 text-lg font-bold">The street</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {[...streets.entries()].map(([street, list]) => (
              <div key={street} className="rounded-md border-2 border-line/60 p-2">
                <p className="mb-2 border-b-4 border-dashed border-line/50 pb-1 text-sm font-bold text-muted">{street}</p>
                <ul className="grid grid-cols-2 gap-2">
                  {list.map((d) => (
                    <li key={d.resident.id} className="flex min-w-0 flex-col">
                      {tile(d)}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/** The same doors as a plain list: easier with a screen reader, and the cut line if a grid won't do. */
export function DoorList({
  doors,
  filter,
  onSelect,
}: {
  doors: Door[];
  filter: Group | null;
  onSelect: (id: string) => void;
}) {
  const order: Record<Group, number> = { urgent: 0, needs_help: 1, waiting: 2, ok: 3 };
  const shown = doors
    .filter((d) => filter === null || d.status.group === filter)
    .sort((a, b) => order[a.status.group] - order[b.status.group]);
  return (
    <ul className="divide-y-2 divide-faint rounded-md border-2 border-line bg-porch">
      {shown.map(({ resident, status }) => (
        <li key={resident.id}>
          <button
            type="button"
            onClick={() => onSelect(resident.id)}
            className="flex min-h-14 w-full items-center gap-3 px-3 py-2 text-left"
          >
            <span className={`door-${status.tone} flex h-10 w-10 shrink-0 items-center justify-center rounded border-2`} aria-hidden>
              <ToneIcon tone={status.tone} />
            </span>
            <span className="flex-1">
              <span className="font-bold">{resident.first_name}</span>{" "}
              <span className="text-muted">· {whereLabel(resident)}</span>
              <span className="block text-sm">
                <strong>{status.label}</strong> · {status.detail}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
