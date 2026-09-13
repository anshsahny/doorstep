import { useCallback, useEffect, useState } from "react";
import { StartDrill } from "../components/StartDrill";
import { ApiError, readReport } from "../lib/api";
import { useIncident } from "../lib/incident";
import { toolWords } from "../lib/status";
import type { Report as ReportData } from "../lib/types";

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

const OUTCOME_WORDS: Record<string, string> = {
  OK: "OK",
  NEEDS_HELP: "needed help",
  URGENT: "urgent",
  NO_ANSWER: "did not answer",
  UNCLEAR: "unclear",
  NOT_REACHED: "not reached yet",
};

export function Report() {
  const { session, recorded, residents, lastUpdated } = useIncident();
  const [report, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (recorded) {
        const r = await fetch("/recorded-report.json");
        if (!r.ok) throw new Error("The recorded report could not be loaded.");
        setReport(await r.json());
      } else if (session) {
        setReport(await readReport(session.incidentId, session.token));
      }
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "The report could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [session, recorded]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!session && !recorded) {
    return (
      <div className="flex max-w-2xl flex-col gap-4">
        <h1 tabIndex={-1} className="text-2xl font-bold">
          Incident report
        </h1>
        <p>The report fills in as your drill runs: who was reached, how fast, and which calls a person made.</p>
        <StartDrill size="small" />
      </div>
    );
  }

  const name = (id: string) => residents.find((r) => r.id === id)?.first_name ?? id;
  const doorstepMinutes = report ? (report.seconds_to_reach_everyone ?? report.seconds_to_last_reached ?? 0) / 60 : 0;
  const treeMinutes = report?.phone_tree.minutes ?? 0;
  const scale = Math.max(treeMinutes, doorstepMinutes, 1);

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 tabIndex={-1} className="text-2xl font-bold">
          Incident report
        </h1>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="min-h-12 rounded-md border-2 border-ink bg-porch px-4 font-bold disabled:opacity-60"
        >
          {loading ? "Updating…" : "Update the report"}
        </button>
      </div>
      {error && (
        <p role="alert" className="rounded-md border-2 border-check bg-check-bg p-3 font-bold">
          {error}
        </p>
      )}
      {!report ? (
        <p>{loading ? "Reading the drill…" : ""}</p>
      ) : (
        <>
          <section aria-labelledby="headline" className="rounded-md border-4 border-ink bg-porch p-5">
            <h2 id="headline" className="text-xl font-bold">
              {report.all_reached ? (
                <>
                  Doorstep reached all <span className="num">{report.residents}</span> neighbours in{" "}
                  <span className="num">{duration(report.seconds_to_reach_everyone)}</span>.
                </>
              ) : (
                <>
                  <span className="num">{report.reached}</span> of <span className="num">{report.residents}</span> neighbours reached so
                  far, in <span className="num">{duration(report.seconds_to_last_reached)}</span>.
                </>
              )}
            </h2>
            <p className="mt-1">
              A phone tree with {report.phone_tree.volunteers_calling} volunteer calling {report.phone_tree.residents} people at{" "}
              {report.phone_tree.minutes_per_call} minutes each takes about <strong className="num">{Math.round(treeMinutes)} minutes</strong>.
            </p>
            {!report.all_reached && report.not_reached.length > 0 && (
              <p className="mt-1">Still to reach: {report.not_reached.map(name).join(", ")}.</p>
            )}
            <div className="mt-4 flex flex-col gap-2" aria-hidden>
              {[
                { label: "Doorstep", minutes: doorstepMinutes, cls: "bg-ink" },
                { label: "Phone tree", minutes: treeMinutes, cls: "bg-line" },
              ].map((bar) => (
                <div key={bar.label} className="flex items-center gap-3">
                  <span className="w-28 shrink-0 text-base font-bold">{bar.label}</span>
                  <span className="h-6 rounded-sm" style={{ width: `${Math.max(1.5, (bar.minutes / scale) * 100)}%` }}>
                    <span className={`block h-full rounded-sm ${bar.cls}`} />
                  </span>
                  <span className="num shrink-0 text-base">{duration(bar.minutes * 60)}</span>
                </div>
              ))}
            </div>
          </section>

          <div className="grid gap-4 md:grid-cols-2">
            <section aria-labelledby="outcomes" className="rounded-md border-2 border-line bg-porch p-4">
              <h2 id="outcomes" className="text-lg font-bold">
                What the calls found
              </h2>
              <ul className="mt-2">
                {Object.entries(report.outcomes)
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, v]) => (
                    <li key={k}>
                      <strong className="num">{v}</strong> {OUTCOME_WORDS[k] ?? k.toLowerCase()}
                    </li>
                  ))}
              </ul>
              <p className="mt-2 text-base">
                First call <span className="num">{duration(report.seconds_to_first_call)}</span> after the alert arrived.
              </p>
            </section>

            <section aria-labelledby="people" className="rounded-md border-2 border-line bg-porch p-4">
              <h2 id="people" className="text-lg font-bold">
                People and automation
              </h2>
              <ul className="mt-2">
                <li>
                  <strong className="num">{report.automated_actions}</strong> actions taken by the agents, each checked by policy
                </li>
                <li>
                  <strong className="num">{report.decisions.raised}</strong> decisions handed to a person
                </li>
                <li>
                  <strong className="num">{report.decisions.answered_by_people}</strong> answered by a person
                  {Object.keys(report.decisions.by_channel).length > 0 &&
                    ` (${Object.entries(report.decisions.by_channel)
                      .map(([k, v]) => `${v} on ${k === "web" ? "the dashboard" : k === "telegram" ? "Telegram" : k}`)
                      .join(", ")})`}
                </li>
                {report.decisions.waiting > 0 && (
                  <li>
                    <strong className="num">{report.decisions.waiting}</strong> still waiting
                  </li>
                )}
                {report.decisions.expired > 0 && (
                  <li>
                    <strong className="num">{report.decisions.expired}</strong> expired unanswered
                  </li>
                )}
              </ul>
            </section>
          </div>

          {report.urgent.length > 0 && (
            <section aria-labelledby="urgent" className="rounded-md border-2 border-urgent bg-porch p-4">
              <h2 id="urgent" className="text-lg font-bold">
                Urgent calls
              </h2>
              <ul className="mt-2">
                {report.urgent.map((u) => (
                  <li key={u.resident_id}>
                    <strong>{name(u.resident_id)}</strong>: captain paged{" "}
                    {u.call_to_page_seconds !== null ? (
                      <>
                        <span className="num">{duration(u.call_to_page_seconds)}</span> after the call began
                      </>
                    ) : (
                      "during the call"
                    )}
                    {u.flagged_mid_call ? ", while the call was still going" : ""}.
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="refusals" className="rounded-md border-2 border-line bg-porch p-4">
            <h2 id="refusals" className="text-lg font-bold">
              Refused by policy (<span className="num">{report.policy_denials.length}</span>)
            </h2>
            {report.policy_denials.length === 0 ? (
              <p>No action was refused in this drill.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1">
                {report.policy_denials.map((d) => (
                  <li key={d.seq}>
                    <strong>{toolWords(d.tool) || "A message"}</strong>
                    {d.resident_id ? ` for ${name(d.resident_id)}` : ""}: {d.reason}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <p className="text-sm text-muted">
            Report for <code>{report.incident_id}</code>
            {lastUpdated ? "" : ""}. All residents are fictional.
          </p>
        </>
      )}
    </div>
  );
}
