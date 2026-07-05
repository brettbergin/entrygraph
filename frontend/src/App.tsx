import { useState } from "react";
import { Breadcrumbs, Button, Header, Heading, Label } from "@primer/react";
import { api, clearToken, getApiBase, getToken } from "./api";
import { RepoDetail } from "./components/RepoDetail";
import { Counts, EmptyState, ErrorFlash, Loading, StatusLabel, fmtTime, shortSha, useAsync } from "./components/ui";
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
    <>
      <Header>
        <Header.Item>
          <Header.Link
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setNav({ view: "installations" });
            }}
            style={{ fontSize: 16 }}
          >
            entrygraph <span className="accent">Sentinel</span>
          </Header.Link>
        </Header.Item>
        <Header.Item full />
        <Header.Item className="mono muted fs0">{getApiBase() || "same origin"}</Header.Item>
        <Header.Item>
          <Button variant="invisible" onClick={onAuthError}>
            Sign out
          </Button>
        </Header.Item>
      </Header>

      <div className="app">
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
    </>
  );
}

function Breadcrumb({ nav, setNav }: { nav: Nav; setNav: (n: Nav) => void }) {
  const items: { label: string; go?: () => void }[] = [
    { label: "Installations", go: () => setNav({ view: "installations" }) },
  ];
  if (nav.view === "repos" || nav.view === "repo") {
    const inst = nav.inst;
    items.push({
      label: inst.account_login,
      go: nav.view === "repo" ? () => setNav({ view: "repos", inst }) : undefined,
    });
  }
  if (nav.view === "repo") items.push({ label: nav.fullName });

  return (
    <Breadcrumbs>
      {items.map((c, i) => (
        <Breadcrumbs.Item
          key={i}
          href="#"
          selected={i === items.length - 1}
          onClick={(e) => {
            e.preventDefault();
            c.go?.();
          }}
        >
          {c.label}
        </Breadcrumbs.Item>
      ))}
    </Breadcrumbs>
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
  if (error) return <ErrorFlash message={error} />;
  if (loading) return <Loading label="Loading installations…" />;
  if (!data || data.length === 0)
    return (
      <EmptyState title="No installations yet.">
        Install the GitHub App on a repo to get started.
      </EmptyState>
    );
  return (
    <div className="grid">
      {data.map((i) => (
        <div key={i.id} className="card tile" onClick={() => onOpen(i)}>
          <Heading as="h3" className="mb1" style={{ fontSize: 16 }}>
            {i.account_login} {i.suspended && <Label variant="attention">suspended</Label>}
          </Heading>
          <span className="muted fs0">
            {i.repo_count} repo{i.repo_count === 1 ? "" : "s"} · installation #{i.id}
          </span>
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
  if (error) return <ErrorFlash message={error} />;
  if (loading) return <Loading label="Loading repos…" />;
  if (!data || data.length === 0)
    return <EmptyState title={`No scanned repos yet for ${inst.account_login}.`} />;
  return (
    <div className="grid">
      {data.map((r) => {
        const owner = r.full_name.split("/")[0];
        const name = r.full_name.slice(owner.length + 1);
        return (
          <div key={r.full_name} className="card tile" onClick={() => onOpen(r.full_name)}>
            <Heading as="h3" className="mb2" style={{ fontSize: 16 }}>
              {name} <span className="muted" style={{ fontWeight: 400 }}>{owner}/</span>
            </Heading>
            {r.latest_scan ? (
              <div className="stack">
                <div className="row">
                  <StatusLabel status={r.latest_scan.status} />
                  <span className="mono muted">{shortSha(r.latest_scan.head_sha)}</span>
                </div>
                <Counts counts={r.latest_scan.counts} />
                <span className="muted fs0">{fmtTime(r.latest_scan.created_at)}</span>
              </div>
            ) : (
              <span className="muted fs0">No scans yet</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
