import { useEffect, useState } from "react";
import { Header, Label, Select } from "@primer/react";
import { UnderlineNav } from "@primer/react";
import { api } from "./api";
import { Entrypoints } from "./components/Entrypoints";
import { GraphView } from "./components/GraphView";
import { Reachability } from "./components/Reachability";
import { Symbols } from "./components/Symbols";
import { Empty, ErrorFlash, Loading, useAsync } from "./components/ui";
import type { Repo, StatsResponse } from "./types";

type Tab = "overview" | "symbols" | "entrypoints" | "reachability" | "graph";
const TABS: Tab[] = ["overview", "symbols", "entrypoints", "reachability", "graph"];

export function App() {
  const { data: repos, error, loading } = useAsync<Repo[]>(() => api.repos(), []);
  const [repoId, setRepoId] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [graphQ, setGraphQ] = useState<string>("");

  useEffect(() => {
    if (repos && repoId === null && repos.length) setRepoId(repos[0].id);
  }, [repos, repoId]);

  const openGraph = (qname: string) => {
    setGraphQ(qname);
    setTab("graph");
  };

  return (
    <>
      <Header>
        <Header.Item>
          <Header.Link href="#" onClick={(e) => e.preventDefault()} style={{ fontSize: 16 }}>
            entrygraph <span className="accent">explorer</span>
          </Header.Link>
        </Header.Item>
        <Header.Item full />
        {repos && repos.length > 0 && (
          <Header.Item>
            <Select
              value={repoId ?? ""}
              onChange={(e) => {
                setRepoId(Number(e.target.value));
                setTab("overview");
              }}
            >
              {repos.map((r) => (
                <Select.Option key={r.id} value={String(r.id)}>
                  {r.name} ({r.symbols} symbols)
                </Select.Option>
              ))}
            </Select>
          </Header.Item>
        )}
      </Header>

      <div className="app">
        {error ? (
          <ErrorFlash message={error} />
        ) : loading || !repos ? (
          <Loading label="Loading index…" />
        ) : repos.length === 0 ? (
          <Empty>This index has no repos. Index one with `entrygraph index &lt;path&gt;`.</Empty>
        ) : repoId === null ? (
          <Loading />
        ) : (
          <>
            <UnderlineNav aria-label="Views">
              {TABS.map((t) => (
                <UnderlineNav.Item
                  key={t}
                  aria-current={tab === t ? "page" : undefined}
                  onSelect={(e) => {
                    e.preventDefault();
                    setTab(t);
                  }}
                >
                  {t[0].toUpperCase() + t.slice(1)}
                </UnderlineNav.Item>
              ))}
            </UnderlineNav>

            {tab === "overview" && <Overview repoId={repoId} />}
            {tab === "symbols" && <Symbols repoId={repoId} onOpenGraph={openGraph} />}
            {tab === "entrypoints" && <Entrypoints repoId={repoId} onOpenGraph={openGraph} />}
            {tab === "reachability" && <Reachability repoId={repoId} />}
            {tab === "graph" && <GraphView repoId={repoId} qname={graphQ} onFocus={setGraphQ} />}
          </>
        )}
      </div>
    </>
  );
}

function Overview({ repoId }: { repoId: number }) {
  const { data, error, loading } = useAsync<StatsResponse>(() => api.stats(repoId), [repoId]);
  if (error) return <ErrorFlash message={error} />;
  if (loading || !data) return <Loading label="Loading stats…" />;
  const s = data.stats;
  const tiles: [string, number][] = [
    ["symbols", s.symbols],
    ["edges", s.edges],
    ["resolved edges", s.resolved_edges],
    ["entrypoints", s.entrypoints],
    ["files", s.files],
    ["source edges", s.source_edges],
    ["sink edges", s.sink_edges],
  ];
  return (
    <div className="section">
      <div className="stats">
        {tiles.map(([l, n]) => (
          <div key={l} className="card stat">
            <div className="n">{n.toLocaleString()}</div>
            <div className="l">{l}</div>
          </div>
        ))}
      </div>

      <div className="split section">
        <div className="card" style={{ padding: 16 }}>
          <div className="l muted" style={{ marginBottom: 8 }}>
            LANGUAGES
          </div>
          {data.languages.length === 0 ? (
            <span className="muted">—</span>
          ) : (
            data.languages.map((lang) => (
              <div key={lang.name} className="row" style={{ padding: "3px 0" }}>
                <span>{lang.name}</span>
                <div className="spacer" />
                <span className="muted fs0">
                  {lang.files} files · {lang.percent}%
                </span>
              </div>
            ))
          )}
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div className="l muted" style={{ marginBottom: 8 }}>
            FRAMEWORKS
          </div>
          {data.frameworks.length === 0 ? (
            <span className="muted">none detected</span>
          ) : (
            <div className="row wrap" style={{ gap: 6 }}>
              {data.frameworks.map((fw) => (
                <Label key={fw.name} variant="accent">
                  {fw.name}
                </Label>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
