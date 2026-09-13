import type { ReactNode } from "react";
import { useIncident } from "../lib/incident";
import { Link, usePath } from "../lib/router";

const TABS = [
  { to: "/board", label: "Board" },
  { to: "/decisions", label: "Decisions" },
  { to: "/policies", label: "Policies" },
  { to: "/report", label: "Report" },
  { to: "/evidence", label: "Evidence" },
];

export function Layout({ children }: { children: ReactNode }) {
  const path = usePath();
  const { decisions, recorded, session } = useIncident();
  const waiting = decisions.filter((d) => d.status === "pending").length;

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-10 focus:bg-porch focus:p-3">
        Skip to content
      </a>
      <header className="border-b-4 border-ink bg-porch">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 text-xl font-bold text-ink no-underline">
            <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
              <rect width="32" height="32" rx="6" fill="#1C2328" />
              <rect x="9" y="6" width="14" height="22" rx="1.5" fill="#F4F1EA" />
              <circle cx="19.5" cy="17.5" r="1.6" fill="#1C2328" />
              <rect x="6" y="26" width="20" height="2.5" fill="#A3241C" />
            </svg>
            Doorstep
          </Link>
          <nav aria-label="Main" className="-mx-1 flex flex-wrap gap-1">
            {TABS.map((t) => {
              const current = path === t.to;
              return (
                <Link
                  key={t.to}
                  to={t.to + (recorded ? "?recorded=1" : "")}
                  aria-current={current ? "page" : undefined}
                  className={`flex min-h-12 items-center gap-2 rounded-md px-3 text-base font-bold no-underline ${
                    current ? "bg-ink text-porch" : "text-ink hover:bg-faint"
                  }`}
                >
                  {t.label}
                  {t.to === "/decisions" && waiting > 0 && (
                    <span className={`num rounded-full px-2 text-sm ${current ? "bg-porch text-ink" : "bg-urgent text-porch"}`}>
                      {waiting}
                      <span className="sr-only"> waiting</span>
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>
          <p className="ml-auto text-sm text-muted">
            {recorded ? "Recorded drill" : session?.scope === "captain" ? "Captain mode" : session ? "Your sandbox drill" : "Demo"}
          </p>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-5">
        {children}
      </main>
      <footer className="border-t-2 border-line/40 bg-porch">
        <div className="mx-auto flex max-w-7xl flex-wrap justify-between gap-2 px-4 py-4 text-sm text-muted">
          <p>
            Residents, volunteers and the neighbourhood team are fictional. The 2021 National Weather Service alert text is
            real.
          </p>
          <p className="font-bold text-ink">Built with Strands Agents on Amazon Bedrock AgentCore</p>
        </div>
      </footer>
    </div>
  );
}
