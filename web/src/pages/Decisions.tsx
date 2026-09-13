import { DecisionCard } from "../components/DecisionCard";
import { StartDrill } from "../components/StartDrill";
import { useIncident } from "../lib/incident";

export function Decisions() {
  const { session, recorded, decisions, incident } = useIncident();
  const visible = decisions.filter((d) => d.status !== "draft");
  const pending = visible.filter((d) => d.status === "pending");
  const done = visible.filter((d) => d.status !== "pending").reverse();

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <h1 tabIndex={-1} className="text-2xl font-bold">
        Decisions
      </h1>
      <p>
        Doorstep handles the routine calls itself and stops for a person only when a person should decide: someone may be in danger,
        a volunteer is about to knock on a door, or a need is beyond what it can arrange.
        {incident?.mode === "sandbox" &&
          " In your drill you play Maria, the block captain, and any volunteer a task is addressed to."}
      </p>

      {!session && !recorded ? (
        <div className="flex flex-col gap-3 rounded-md border-2 border-dashed border-line p-4">
          <p className="text-lg">Nothing to decide yet. Start a drill and the first decisions arrive within a minute.</p>
          <StartDrill size="small" />
        </div>
      ) : (
        <>
          <section aria-labelledby="waiting-heading">
            <h2 id="waiting-heading" className="mb-2 text-xl font-bold">
              Waiting for you (<span className="num">{pending.length}</span>)
            </h2>
            {pending.length === 0 ? (
              <p className="rounded-md border-2 border-dashed border-line p-4">
                {visible.length === 0
                  ? "Nothing yet. Decisions appear here as soon as a call needs one, usually within the first minute."
                  : "All caught up. New decisions will appear here."}
              </p>
            ) : (
              <ul className="flex flex-col gap-3">
                {pending.map((d) => (
                  <DecisionCard key={d.id} decision={d} />
                ))}
              </ul>
            )}
          </section>
          {done.length > 0 && (
            <section aria-labelledby="done-heading">
              <h2 id="done-heading" className="mb-2 text-xl font-bold">
                Already decided
              </h2>
              <ul className="flex flex-col gap-2">
                {done.map((d) => (
                  <DecisionCard key={d.id} decision={d} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
