import { StartDrill } from "../components/StartDrill";
import { useIncident } from "../lib/incident";
import { Link, navigate } from "../lib/router";

const STEPS = [
  { t: "Run a drill", d: "A real, archived weather warning arrives. Doorstep decides who to call first." },
  { t: "Answer one call yourself", d: "Play a neighbour by voice. Say you feel dizzy and hear what happens." },
  { t: "Make the call a person should make", d: "Approve or redirect a volunteer visit, just as the captain would on her phone." },
  { t: "Read the report", d: "Who was reached, how fast, and which decisions stayed with people." },
];

export function Home() {
  const { session, useRecorded } = useIncident();
  return (
    <div className="flex flex-col gap-8">
      <section className="grid items-center gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <h1 tabIndex={-1} className="text-2xl font-bold sm:text-[2.75rem] sm:leading-tight">
            When a heat warning hits, Doorstep knocks on every door that matters.
          </h1>
          <p className="max-w-prose text-lg">
            It phones every at-risk neighbour on a volunteer group's list, sorts who is OK from who isn't, sends a volunteer to the
            doors that need a knock, and interrupts the block captain only for the decisions a person must make.
          </p>
          <p className="max-w-prose">
            Built for volunteer neighbourhood teams, and for the older and isolated neighbours they look out for.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            {session ? (
              <button type="button" onClick={() => navigate("/board")} className="min-h-16 rounded-md bg-ink px-8 text-xl font-bold text-porch">
                Back to your drill
              </button>
            ) : (
              <StartDrill />
            )}
            <button
              type="button"
              className="min-h-12 font-bold underline"
              onClick={() => {
                useRecorded(true);
                navigate("/board?recorded=1");
              }}
            >
              Or watch a recorded drill
            </button>
          </div>
          <p className="text-base text-muted">
            About four minutes. Twelve fictional neighbours. Nobody real is called or messaged.
          </p>
        </div>
        <ol className="flex flex-col gap-3" aria-label="What you'll do">
          {STEPS.map((s, i) => (
            <li key={s.t} className="flex gap-3 rounded-md border-2 border-line bg-porch p-3">
              <span className="num flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-lg font-bold text-porch" aria-hidden>
                {i + 1}
              </span>
              <span>
                <strong className="block text-lg">{s.t}</strong>
                {s.d}
              </span>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="why" className="grid gap-4 md:grid-cols-3">
        <h2 id="why" className="sr-only">
          How it stays safe
        </h2>
        {[
          {
            t: "People decide what matters",
            d: "Routine calls run on their own. Anything that sends a person to a door, or might be an emergency, waits for the captain.",
          },
          {
            t: "Rules it cannot break",
            d: "Every call and message is checked by written Cedar policies first. Refusals are logged with a reason.",
            link: "/policies",
          },
          {
            t: "It never calls 911 itself",
            d: "It tells the resident to call 911 and pages a person at once, while the call is still going.",
          },
        ].map((c) => (
          <div key={c.t} className="rounded-md border-2 border-line bg-porch p-4">
            <h3 className="text-lg font-bold">{c.t}</h3>
            <p className="mt-1">{c.d}</p>
            {c.link && (
              <Link to={c.link} className="mt-2 inline-block min-h-12 py-2 font-bold">
                Read the rules
              </Link>
            )}
          </div>
        ))}
      </section>

      <p className="text-base">
        Running the live demo?{" "}
        <Link to="/captain" className="inline-flex min-h-12 items-center">
          Captain mode
        </Link>
      </p>
    </div>
  );
}
