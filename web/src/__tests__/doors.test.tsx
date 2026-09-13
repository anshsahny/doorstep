import { fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import recorded from "../../public/recorded-drill.json";
import { CounterStrip, DoorGrid, DoorList, useDoors } from "../components/DoorGrid";
import type { Group } from "../components/DoorGrid";
import { doorStatus } from "../lib/status";
import type { CaseView, Snapshot, Volunteer } from "../lib/types";

const snap = recorded as unknown as Snapshot;
const volunteers = snap.volunteers as Volunteer[];

function caseWith(partial: Partial<CaseView>): CaseView {
  return {
    resident_id: "r01",
    state: "QUEUED",
    attempts: 0,
    risk: { points: 1, wave: 1, factors: [] },
    result: null,
    attempt_log: [],
    assigned_volunteer: null,
    outcome: null,
    updated_at: "",
    history: [],
    ...partial,
  };
}

describe("door status", () => {
  it("gives every state a word and a detail, never colour alone", () => {
    const states: CaseView["state"][] = ["QUEUED", "CALLING", "OK", "NEEDS_HELP", "URGENT", "NO_ANSWER", "UNCLEAR", "ASSIGNED", "ESCALATED", "RESOLVED"];
    for (const state of states) {
      const s = doorStatus(caseWith({ state, attempts: 1 }), { voice: false, volunteers });
      expect(s.label.length, state).toBeGreaterThan(1);
      expect(s.detail.length, state).toBeGreaterThan(1);
    }
  });

  it("keeps an urgent door marked after the captain handles it", () => {
    const handled = caseWith({
      state: "RESOLVED",
      outcome: "captain_handling",
      result: { status: "URGENT", needs: [], red_flags: ["confusion"], summary: "", key_quote: "", flagged_mid_call: true, confidence: 1, language: "en", backstop: null },
      history: [{ from_state: "CALLING", to_state: "URGENT", at: "", reason: "" }],
    });
    const s = doorStatus(handled, { voice: false, volunteers });
    expect(s.tone).toBe("urgent");
    expect(s.detail).toBe("Captain handled it");
  });

  it("invites the visitor to answer the reserved voice call", () => {
    expect(doorStatus(undefined, { voice: true, volunteers }).label).toBe("Waiting for you");
  });

  it("names the volunteer who is on the way", () => {
    const s = doorStatus(caseWith({ state: "ASSIGNED", assigned_volunteer: "vol-tom" }), { voice: false, volunteers });
    expect(s.detail).toBe("Tom is going");
  });
});

function Harness({ filter = null }: { filter?: Group | null }) {
  const doors = useDoors(snap.residents!, snap.cases, volunteers, snap.incident.voice_residents);
  return (
    <main>
      <h1>Board</h1>
      <h2>Doors</h2>
      <CounterStrip doors={doors} filter={filter} onFilter={() => {}} />
      <DoorGrid doors={doors} selected={null} filter={filter} onSelect={() => {}} />
      <DoorList doors={doors} filter={filter} onSelect={() => {}} />
    </main>
  );
}

describe("door grid", () => {
  it("renders one named, keyboard-reachable door per resident with its status in the name", () => {
    render(<Harness />);
    const building = screen.getByRole("region", { name: /Juniper Court/ });
    const doors = within(building).getAllByRole("button");
    const street = screen.getByRole("region", { name: /surrounding streets/ });
    expect(doors.length + within(street).getAllByRole("button").length).toBe(12);
    for (const door of doors) {
      expect(door.textContent).toMatch(/: (OK|Urgent|Needs help|Helped|No answer|Not sure yet|Calling…|Not called yet|Waiting for you|On the call)\. /);
    }
  });

  it("dims, but does not hide, doors outside the filter", () => {
    render(<Harness filter="urgent" />);
    const dimmed = document.querySelectorAll(".door.door-dimmed");
    const urgent = document.querySelectorAll('.door[data-tone="urgent"]:not(.door-dimmed)');
    expect(urgent.length).toBeGreaterThan(0);
    expect(dimmed.length + urgent.length).toBe(12);
  });

  it("passes axe with no violations (contrast is checked in Lighthouse on the real page)", async () => {
    const { container } = render(<Harness />);
    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
  });

  it("toggles a filter with the keyboard", () => {
    let chosen: Group | null = null;
    function Strip() {
      const doors = useDoors(snap.residents!, snap.cases, volunteers, []);
      return <CounterStrip doors={doors} filter={null} onFilter={(g) => (chosen = g)} />;
    }
    render(<Strip />);
    const urgent = screen.getByRole("button", { name: /urgent/ });
    urgent.focus();
    fireEvent.click(urgent);
    expect(chosen).toBe("urgent");
    expect(urgent).toHaveAttribute("aria-pressed", "false");
  });
});

describe("motion", () => {
  it("pulses an urgent door only when the visitor has not asked for reduced motion", () => {
    const css = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
    const allowed = css.indexOf("@media (prefers-reduced-motion: no-preference)");
    const knock = css.indexOf("animation: door-knock");
    expect(allowed).toBeGreaterThan(-1);
    expect(knock).toBeGreaterThan(allowed);
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });
});
