import policies from "../generated/policies.json";
import { Shield } from "../components/Icons";
import { clock } from "../components/ResidentPanel";
import { useIncident } from "../lib/incident";
import { toolWords } from "../lib/status";

interface Policy {
  id: string;
  file: string;
  cedar: string;
  title: string;
  kind: string;
  plain: string;
  protects: string;
  checked_by: string[];
}

export function Policies() {
  const { events, residents, recorded, session } = useIncident();
  const denials = events.filter((e) => e.policy_decision === "deny" && e.type === "policy");
  const name = (id: string | null) => residents.find((r) => r.id === id)?.first_name;

  return (
    <div className="flex flex-col gap-5">
      <div className="max-w-3xl">
        <h1 tabIndex={-1} className="text-2xl font-bold">
          The rules Doorstep cannot break
        </h1>
        <p className="mt-2">
          The agent proposes; these rules decide. Every action that reaches the outside world, like a phone call, a volunteer visit
          or a message, is checked against written policies before it runs. They are written in <strong>Cedar</strong>, a policy
          language that can be read and tested. A refusal is logged with its reason, and the agent cannot talk its way around it.
        </p>
        <p className="mt-2">
          Anything no rule permits is refused. Real calls are also checked against the approved call list a second time in code, just
          before dialling.
        </p>
      </div>

      <section aria-labelledby="denials-heading" className="rounded-md border-2 border-ink bg-porch p-4">
        <h2 id="denials-heading" className="text-xl font-bold">
          Refused in {recorded ? "the recorded drill" : session ? "your drill" : "a drill"}
        </h2>
        {!session && !recorded ? (
          <p>Run a drill to see refusals as they happen.</p>
        ) : denials.length === 0 ? (
          <p>
            Nothing has been refused so far. Refusals are rare when the agent follows the rules, which is the point: the rules still
            run on every action.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {denials.map((e) => (
              <li key={e.seq} className="rounded border-l-4 border-urgent bg-urgent-bg py-2 pl-3 pr-2">
                <p className="text-sm text-muted">
                  <span className="num">{clock(e.at)}</span> · {e.by === "code" ? "Code check" : "Cedar policy"}
                  {name(e.resident_id) ? ` · about ${name(e.resident_id)}` : ""}
                </p>
                <p className="font-bold">Refused: {toolWords(e.tool) || "sending a message"}</p>
                <p className="text-base">{e.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <ul className="flex flex-col gap-4">
        {(policies as Policy[]).map((p) => (
          <li key={p.id}>
            <article aria-labelledby={`policy-${p.id}`} className="grid overflow-hidden rounded-md border-2 border-line bg-porch lg:grid-cols-2">
              <div className="p-4">
                <p className="flex items-center gap-2 text-sm font-bold text-muted">
                  <Shield /> {p.kind === "forbid" ? "Forbids, and wins over every permit" : "Permits only when"}
                </p>
                <h2 id={`policy-${p.id}`} className="mt-1 text-lg font-bold">
                  {p.title}
                </h2>
                <p className="mt-2">{p.plain}</p>
                <p className="mt-2 text-base">
                  <strong>Protects:</strong> {p.protects}
                </p>
                <div className="mt-2 text-base">
                  <strong>Tested on every build:</strong>
                  <ul className="ml-5 list-disc">
                    {p.checked_by.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="min-w-0 border-t-2 border-line bg-ink p-4 text-porch lg:border-l-2 lg:border-t-0">
                <p className="text-sm font-bold">
                  Cedar · <code>{p.file}</code>
                </p>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-sm leading-relaxed" tabIndex={0} aria-label={`Cedar text of ${p.title}`}>
                  <code>{p.cedar}</code>
                </pre>
              </div>
            </article>
          </li>
        ))}
      </ul>
    </div>
  );
}
