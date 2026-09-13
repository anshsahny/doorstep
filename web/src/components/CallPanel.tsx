// "Answer a call as a resident": the judge talks to Doorstep by voice (Nova 2 Sonic), and the
// result lands on their own drill's board like any other check-in.

import { useEffect, useRef, useState } from "react";
import { apiUrl } from "../lib/api";
import { whereLabel } from "../lib/status";
import type { Resident } from "../lib/types";
import { Mic, Stop } from "./Icons";
import { startCall } from "../voice-client.js";

type Phase = "idle" | "starting" | "live" | "ended" | "error";

interface Line {
  who: "agent" | "resident" | "note";
  text: string;
}

export function CallPanel({
  resident,
  incidentId,
  token,
  answered,
  disabled,
}: {
  resident: Resident;
  incidentId: string;
  token: string;
  answered: boolean;
  disabled?: string;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [seconds, setSeconds] = useState(0);
  const call = useRef<{ hangUp: () => void; maxSeconds: number } | null>(null);
  const logRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (phase !== "live") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [phase]);

  useEffect(() => {
    logRef.current?.lastElementChild?.scrollIntoView({ block: "nearest" });
  }, [lines]);

  useEffect(() => () => call.current?.hangUp(), []);

  const begin = async () => {
    setPhase("starting");
    setStatus("Asking for your microphone…");
    setLines([]);
    setSeconds(0);
    try {
      call.current = await startCall({
        apiUrl: await apiUrl(),
        token,
        incidentId,
        residentId: resident.id,
        on: {
          status: (s: string) => {
            if (s === "connected") {
              setPhase("live");
              setStatus("On the call. Speak normally; you can interrupt.");
            } else if (!s.startsWith("audio ready")) {
              setStatus(s.charAt(0).toUpperCase() + s.slice(1));
            }
          },
          transcript: (who: "agent" | "resident", text: string) =>
            setLines((prev) => [...prev, { who, text }]),
          error: (m: string) => setLines((prev) => [...prev, { who: "note", text: m }]),
          ended: () => {
            setPhase("ended");
            setStatus("Call ended. Your answers are on the board in a few seconds.");
            call.current = null;
          },
        },
      });
      setPhase((p) => (p === "starting" ? "live" : p));
    } catch (err) {
      setPhase("error");
      setStatus(err instanceof Error ? err.message : "The call could not start.");
    }
  };

  const hangUp = () => {
    call.current?.hangUp();
    setStatus("Hanging up…");
  };

  const live = phase === "live" || phase === "starting";
  return (
    <section aria-labelledby="call-heading" className="rounded-md border-4 border-calling bg-calling-bg p-4">
      <h2 id="call-heading" className="flex items-center gap-2 text-lg font-bold">
        <Mic /> {answered && phase === "idle" ? `You answered as ${resident.first_name}` : `${resident.first_name}'s phone is ringing`}
      </h2>
      {phase === "idle" && !answered && (
        <>
          <p className="mt-1">
            Play {resident.first_name} (fictional, {resident.age_band}, {whereLabel(resident)}). Doorstep will ask how you are. Answer
            however you like. To hear it page the captain, say something like{" "}
            <strong>“I feel dizzy and a bit confused.”</strong>
          </p>
          <p className="mt-1 text-sm">About a minute. Uses your microphone. Nothing is recorded outside this drill.</p>
        </>
      )}
      {disabled ? (
        <p className="mt-3 font-bold">{disabled}</p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {!live ? (
            <button
              type="button"
              onClick={begin}
              disabled={answered && phase !== "error"}
              className="inline-flex min-h-14 items-center gap-2 rounded-md bg-calling px-5 text-lg font-bold text-porch disabled:opacity-60"
            >
              <Mic /> {phase === "error" ? "Try the call again" : answered ? "Call finished" : `Answer as ${resident.first_name}`}
            </button>
          ) : (
            <button
              type="button"
              onClick={hangUp}
              className="inline-flex min-h-14 items-center gap-2 rounded-md bg-urgent px-5 text-lg font-bold text-porch"
            >
              <Stop /> Hang up
            </button>
          )}
          {phase === "live" && (
            <span className="num text-base" aria-label={`${seconds} seconds`}>
              {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")} / 3:00
            </span>
          )}
        </div>
      )}
      <p className="mt-2" role="status" aria-live="polite">
        {status}
      </p>
      {lines.length > 0 && (
        <ol ref={logRef} className="mt-2 max-h-56 overflow-y-auto rounded border-2 border-calling/40 bg-porch p-2" aria-label="Live transcript">
          {lines.map((l, i) => (
            <li key={i} className={l.who === "note" ? "text-muted italic" : ""}>
              {l.who !== "note" && <strong>{l.who === "agent" ? "Doorstep" : "You"}: </strong>}
              {l.text}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
