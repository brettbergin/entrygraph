import { Heading, Label, UnderlineNav } from "@primer/react";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useLocation, useParams } from "react-router";
import { api, keys } from "../../api/queries";
import { ErrorFlash, Loading } from "../../components/ui";
import type { Repo } from "../../api/types";

const TABS = [
  { path: "", label: "Overview" },
  { path: "symbols", label: "Symbols" },
  { path: "entrypoints", label: "Entrypoints" },
  { path: "graph", label: "Call graph" },
  { path: "reachability", label: "Reachability" },
];

export function useRepoId(): number {
  const { repoId } = useParams();
  return Number(repoId);
}

export function useRepo(): Repo | undefined {
  const repoId = useRepoId();
  const { data } = useQuery({
    queryKey: keys.repo(repoId),
    queryFn: () => api.repo(repoId),
    enabled: Number.isFinite(repoId),
  });
  return data;
}

export function RepoLayout() {
  const repoId = useRepoId();
  const location = useLocation();
  const {
    data: repo,
    isPending,
    error,
  } = useQuery({ queryKey: keys.repo(repoId), queryFn: () => api.repo(repoId) });

  if (isPending) return <Loading label="Loading repository…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  const base = `/repos/${repoId}`;
  const active = location.pathname.slice(base.length).replace(/^\//, "").split("/")[0];

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <Heading as="h1" style={{ fontSize: 28 }}>
          {repo.name}
        </Heading>
        {repo.sentinel && <Label>sentinel</Label>}
        <span className="muted fs0 mono clip" title={repo.root_path}>
          {repo.root_path}
        </span>
      </div>
      <UnderlineNav aria-label="Repository views">
        {TABS.map((t) => (
          <UnderlineNav.Item
            key={t.path}
            as={Link}
            to={t.path ? `${base}/${t.path}` : base}
            aria-current={active === t.path ? "page" : undefined}
          >
            {t.label}
          </UnderlineNav.Item>
        ))}
      </UnderlineNav>
      <div className="section">
        <Outlet />
      </div>
    </>
  );
}
