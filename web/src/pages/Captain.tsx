import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, captainSession } from "../lib/api";
import { useIncident } from "../lib/incident";
import { navigate } from "../lib/router";
import { captainToken, saveCaptainToken } from "../lib/session";

export function Captain() {
  const { setSession } = useIncident();
  const [passcode, setPasscode] = useState("");
  const [incidentId, setIncidentId] = useState("");
  const [token, setToken] = useState<string | null>(() => captainToken());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const s = await captainSession(passcode);
      saveCaptainToken(s.token);
      setToken(s.token);
      setPasscode("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };

  const open = (e: FormEvent) => {
    e.preventDefault();
    const id = incidentId.trim();
    if (!/^[a-z0-9][a-z0-9-]{2,60}$/.test(id)) {
      setError("That does not look like a drill id, for example drill-20260913-190501-a1b2.");
      return;
    }
    setSession({ incidentId: id, token: token!, voiceResident: "", scope: "captain", startedAt: Date.now() });
    navigate("/board");
  };

  return (
    <div className="flex max-w-xl flex-col gap-4">
      <h1 tabIndex={-1} className="text-2xl font-bold">
        Captain mode
      </h1>
      <p>For the team running the live demo. Judges: the passcode is in the testing instructions.</p>
      {!token ? (
        <form onSubmit={signIn} className="flex flex-col gap-3">
          <label htmlFor="passcode" className="font-bold">
            Captain passcode
          </label>
          <input
            id="passcode"
            type="password"
            autoComplete="current-password"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            className="min-h-12 rounded-md border-2 border-ink bg-porch px-3 text-lg"
            required
          />
          <button type="submit" disabled={busy} className="min-h-12 self-start rounded-md bg-ink px-5 text-lg font-bold text-porch">
            {busy ? "Checking…" : "Sign in"}
          </button>
        </form>
      ) : (
        <form onSubmit={open} className="flex flex-col gap-3">
          <label htmlFor="incident" className="font-bold">
            Drill or incident id
          </label>
          <input
            id="incident"
            value={incidentId}
            onChange={(e) => setIncidentId(e.target.value)}
            className="min-h-12 rounded-md border-2 border-ink bg-porch px-3 text-lg"
            placeholder="drill-20260913-190501-a1b2"
            autoCapitalize="off"
            spellCheck={false}
            required
          />
          <div className="flex flex-wrap gap-3">
            <button type="submit" className="min-h-12 rounded-md bg-ink px-5 text-lg font-bold text-porch">
              Open the board
            </button>
            <button
              type="button"
              className="min-h-12 px-3 underline"
              onClick={() => {
                saveCaptainToken(null);
                setToken(null);
              }}
            >
              Sign out
            </button>
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="font-bold text-urgent">
          {error}
        </p>
      )}
    </div>
  );
}
