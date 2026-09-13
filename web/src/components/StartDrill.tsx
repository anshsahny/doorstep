// "Run a drill": a visitor's own 12-resident sandbox drill. One click, one drill: a retried click
// carries the same idempotency key, so it can never start two.

import { useRef, useState } from "react";
import { ApiError, startDrill } from "../lib/api";
import { useIncident } from "../lib/incident";
import { navigate } from "../lib/router";

function key(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function StartDrill({ size = "large" }: { size?: "large" | "small" }) {
  const { setSession, useRecorded } = useIncident();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const attempt = useRef(key());

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const drill = await startDrill(attempt.current);
      setSession({
        incidentId: drill.incident_id,
        token: drill.token,
        voiceResident: drill.voice_resident,
        scope: "sandbox",
        startedAt: Date.now(),
      });
      attempt.current = key();
      navigate("/board");
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "The drill did not start. Please try again."));
      if (!(err instanceof ApiError) || err.status !== 409) attempt.current = key();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        type="button"
        onClick={start}
        disabled={busy}
        className={`rounded-md bg-ink font-bold text-porch disabled:opacity-70 ${
          size === "large" ? "min-h-16 px-8 text-xl" : "min-h-12 px-5 text-lg"
        }`}
      >
        {busy ? "Starting your drill…" : "Run a drill"}
      </button>
      {error && (
        <div role="alert" className="max-w-prose rounded-md border-2 border-check bg-check-bg p-3">
          <p className="font-bold">{error.message}</p>
          {(error.recordedDrill || error.status === 0 || error.status >= 500) && (
            <p className="mt-1">
              <button
                type="button"
                className="min-h-12 font-bold underline"
                onClick={() => {
                  useRecorded(true);
                  navigate("/board?recorded=1");
                }}
              >
                Watch a recorded drill instead
              </button>
              {error.videoUrl && (
                <>
                  {" "}
                  or <a href={error.videoUrl}>watch the demo video</a>
                </>
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
