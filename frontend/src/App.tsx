import { useState } from "react";
import { api, clearToken, getApiBase, getToken } from "./api";
import { RepoDetail } from "./components/RepoDetail";
import { Badge, Counts, Empty, ErrorBox, fmtTime, shortSha, useAsync } from "./components/ui";
import { TokenGate } from "./components/TokenGate";
import type { Installation, Repo } from "./types";

type Nav =
  | { view: "installations" }
  | { view: "repos"; inst: Installation }
  | { view: "repo"; inst: Installation; fullName: string };

export function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [nav, setNav] = useState<Nav>({ view: "installations" });

  const onAuthError = () => {
    clearToken();
    setAuthed(false);
  };

  if (!authed) {
    return (
      <TokenGate
        onReady={() => {
          setNav({ view: "installations" });
          setAuthed(true);
        }}
      />
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="logo">
          entrygraph <span>Sentinel</span>
        </div>
        <div className="spacer" />
        <span className="base">{getApiBase() || "same origin"}</span>
        <button className="btn ghost" onClick={onAuthError}>
          Sign out
        </button>
      </header>

      <Breadcrumb nav={nav} setNav={setNav} />

      {nav.view === "installations" && (
        <Installations
          onAuthError={onAuthError}
          onOpen={(inst) => setNav({ view: "repos", inst })}
        />
      )}
      {nav.view === "repos" && (
        <Repos
          inst={nav.inst}
          onAuthError={onAuthError}
          onOpen={(fullName) => setNav({ view: "repo", inst: nav.inst, fullName })}
        />
      )}
      {nav.view === "repo" && (
        <RepoDetail
          installationId={nav.inst.id}
          fullName={nav.fullName}
          onAuthError={onAuthError}
        />
      )}
    </div>
  );
}

function Breadcrumb({ nav, setNav }: { nav: Nav; setNav: (n: Nav) => void }) {
  const crumbs: { label: string; go?: () => void }[] = [
    { label: "Installations", go: () => setNav({ view: "installations" }) },
  ];
  if (nav.view === "repos" || nav.view === "repo") {
    const inst = nav.inst;
    crumbs.push({
      label: inst.account_login,
      go: nav.view === "repo" ? () => setNav({ view: "repos", inst }) : undefined,
    });
  }
  if (nav.view === "repo") crumbs.push({ label: nav.fullName });

  return (
    <div className="breadcrumb">
      {crumbs.map((c, i) => (
        <span key={i} style={{ display: "contents" }}>
          {i > 0 && <span className="sep">/</span>}
          {c.go ? <button onClick={c.go}>{c.label}</button> : <span>{c.label}</span>}
        </span>
      ))}
    </div>
  );
}

function Installations({
  onOpen,
  onAuthError,
}: {
  onOpen: (i: Installation) => void;
  onAuthError: () => void;
}) {
  const { data, error, loading } = useAsync<Installation[]>(
    () => api.installations(),
    [],
    onAuthError,
  );
  if (error) return <ErrorBox message={error} />;
  if (loading) return <Empty>Loading installations…</Empty>;
  if (!data || data.length === 0)
    return <Empty>No installations yet. Install the GitHub App on a repo to get started.</Empty>;
  return (
    <div className="grid">
      {data.map((i) => (
        <div key={i.id} className="card tile" onClick={() => onOpen(i)}>
          <h3>
            {i.account_login} {i.suspended && <Badge kind="suspended" />}
          </h3>
          <div className="meta">
            {i.repo_count} repo{i.repo_count === 1 ? "" : "s"} · installation #{i.id}
          </div>
        </div>
      ))}
    </div>
  );
}

function Repos({
  inst,
  onOpen,
  onAuthError,
}: {
  inst: Installation;
  onOpen: (fullName: string) => void;
  onAuthError: () => void;
}) {
  const { data, error, loading } = useAsync<Repo[]>(
    () => api.repos(inst.id),
    [inst.id],
    onAuthError,
  );
  if (error) return <ErrorBox message={error} />;
  if (loading) return <Empty>Loading repos…</Empty>;
  if (!data || data.length === 0)
    return <Empty>No scanned repos yet for {inst.account_login}.</Empty>;
  return (
    <div className="grid">
      {data.map((r) => {
        const owner = r.full_name.split("/")[0];
        const name = r.full_name.slice(owner.length + 1);
        return (
          <div key={r.full_name} className="card tile" onClick={() => onOpen(r.full_name)}>
            <h3>
              {name} <span className="meta">{owner}/</span>
            </h3>
            {r.latest_scan ? (
              <div>
                <div style={{ margin: "8px 0" }}>
                  <Badge kind={r.latest_scan.status} />{" "}
                  <span className="meta mono">{shortSha(r.latest_scan.head_sha)}</span>
                </div>
                <Counts counts={r.latest_scan.counts} />
                <div className="meta" style={{ marginTop: 8 }}>
                  {fmtTime(r.latest_scan.created_at)}
                </div>
              </div>
            ) : (
              <div className="meta" style={{ marginTop: 8 }}>
                No scans yet
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
