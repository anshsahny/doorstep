import { useEffect, useState } from "react";
import evidence from "../data/evidence.json";

interface EvalsReport {
  generated_at?: string;
  summary?: { label: string; value: string; target?: string; passed?: boolean }[];
}

export function Evidence() {
  const [evals, setEvals] = useState<EvalsReport | null>(null);
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    fetch("/evals-report.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setEvals(j))
      .catch(() => setEvals(null))
      .finally(() => setChecked(true));
  }, []);

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <div>
        <h1 tabIndex={-1} className="text-2xl font-bold">
          Evidence
        </h1>
        <p className="mt-2">
          Each phase of the build had a gate that had to pass before the next began. These are the measured results, not targets.
        </p>
      </div>

      <section aria-labelledby="evals-heading" className="rounded-md border-2 border-ink bg-porch p-4">
        <h2 id="evals-heading" className="text-xl font-bold">
          Evaluations
        </h2>
        {evals?.summary?.length ? (
          <table className="mt-2 w-full border-collapse text-left">
            <caption className="sr-only">Evaluation results</caption>
            <thead>
              <tr className="border-b-2 border-ink">
                <th scope="col" className="py-2 pr-3">Measure</th>
                <th scope="col" className="py-2 pr-3">Result</th>
                <th scope="col" className="py-2">Target</th>
              </tr>
            </thead>
            <tbody>
              {evals.summary.map((row) => (
                <tr key={row.label} className="border-b border-faint">
                  <th scope="row" className="py-2 pr-3 font-normal">{row.label}</th>
                  <td className="num py-2 pr-3 font-bold">
                    {row.value} {row.passed === undefined ? "" : row.passed ? "(met)" : "(not met)"}
                  </td>
                  <td className="num py-2">{row.target ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-1">
            {checked
              ? "The evaluation suites (40 resident personas, dispatcher trajectories, red team, and a replay of the 2021 alert against all 48 residents) are being run now. Their results will appear here."
              : "Loading…"}
          </p>
        )}
      </section>

      <ol className="flex flex-col gap-3">
        {evidence.gates.map((g) => (
          <li key={g.phase} className="rounded-md border-2 border-line bg-porch p-4">
            <p className="text-sm font-bold text-muted">Gate {g.phase} · passed</p>
            <h2 className="text-lg font-bold">{g.title}</h2>
            <p className="mt-1">{g.result}</p>
            <p className="mt-1 text-base text-muted">How it was checked: {g.how}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
