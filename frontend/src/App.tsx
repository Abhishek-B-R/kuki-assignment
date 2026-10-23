import { useEffect, useState } from "react";
import { api, type Health } from "./api";
import { AskView } from "./components/AskView";
import { DocumentDetail } from "./components/DocumentDetail";
import { DocumentsView } from "./components/DocumentsView";
import { EvalView } from "./components/EvalView";
import { FyView } from "./components/FyView";

type Route = { tab: "documents" } | { tab: "doc"; id: string } | { tab: "ask" } | { tab: "fy" } | { tab: "evals" };

const TABS: { key: Route["tab"]; label: string }[] = [
  { key: "documents", label: "Documents" },
  { key: "ask", label: "Ask" },
  { key: "fy", label: "Financial year" },
  { key: "evals", label: "Evaluation" },
];

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [head, ...rest] = h.split("/");
  if (head === "doc" && rest[0]) return { tab: "doc", id: decodeURIComponent(rest[0]) };
  if (head === "ask" || head === "fy" || head === "evals") return { tab: head };
  return { tab: "documents" };
}

function go(hash: string) {
  window.location.hash = hash;
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const onHash = () => {
      setRoute(parseHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const load = () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setOffline(false);
        })
        .catch(() => setOffline(true));
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, []);

  const active = route.tab === "doc" ? "documents" : route.tab;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          KuKi <span className="muted">sustainability data</span>
        </div>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <a
              key={t.key}
              role="tab"
              aria-selected={active === t.key}
              className={`tab${active === t.key ? " active" : ""}`}
              href={`#${t.key}`}
            >
              {t.label}
            </a>
          ))}
        </nav>
        <div className="health small">
          {offline ? (
            <span className="error-inline">API unreachable</span>
          ) : health ? (
            <span className="muted">
              {health.model} · extraction {health.extract_version}
            </span>
          ) : null}
        </div>
      </header>
      <main className="main">
        {route.tab === "documents" && <DocumentsView onOpen={(id) => go(`doc/${encodeURIComponent(id)}`)} />}
        {route.tab === "doc" && <DocumentDetail id={route.id} onBack={() => go("documents")} />}
        {/* Ask and FY stay mounted so a pending answer or a loaded report survives tab switches. */}
        <div hidden={route.tab !== "ask"}>
          <AskView active={route.tab === "ask"} />
        </div>
        <div hidden={route.tab !== "fy"}>
          <FyView active={route.tab === "fy"} />
        </div>
        {route.tab === "evals" && <EvalView />}
      </main>
    </div>
  );
}
