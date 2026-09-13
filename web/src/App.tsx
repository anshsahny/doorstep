import { useEffect } from "react";
import { Layout } from "./components/Layout";
import { IncidentProvider, useIncident } from "./lib/incident";
import { usePath } from "./lib/router";
import { Board } from "./pages/Board";
import { Captain } from "./pages/Captain";
import { Decisions } from "./pages/Decisions";
import { Evidence } from "./pages/Evidence";
import { Home } from "./pages/Home";
import { Policies } from "./pages/Policies";
import { Report } from "./pages/Report";

const TITLES: Record<string, string> = {
  "/": "Doorstep · neighbour check-ins on a heat day",
  "/board": "Board · Doorstep",
  "/decisions": "Decisions · Doorstep",
  "/policies": "Policies · Doorstep",
  "/report": "Report · Doorstep",
  "/evidence": "Evidence · Doorstep",
  "/captain": "Captain mode · Doorstep",
};

function Screens() {
  const path = usePath();
  const { recorded, useRecorded } = useIncident();
  useEffect(() => {
    document.title = TITLES[path] ?? "Doorstep";
  }, [path]);
  useEffect(() => {
    const wantsRecorded = new URLSearchParams(location.search).get("recorded") === "1";
    if (wantsRecorded && !recorded) useRecorded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  switch (path) {
    case "/board":
      return <Board />;
    case "/decisions":
      return <Decisions />;
    case "/policies":
      return <Policies />;
    case "/report":
      return <Report />;
    case "/evidence":
      return <Evidence />;
    case "/captain":
      return <Captain />;
    case "/":
      return <Home />;
    default:
      return (
        <div>
          <h1 tabIndex={-1} className="text-2xl font-bold">
            That page is not here
          </h1>
          <p>
            <a href="/">Go to the start</a>
          </p>
        </div>
      );
  }
}

export function App() {
  return (
    <IncidentProvider>
      <Layout>
        <Screens />
      </Layout>
    </IncidentProvider>
  );
}
