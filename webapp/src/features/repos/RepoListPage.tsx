import { Button, Heading, Label } from "@primer/react";
import { PlusIcon, RepoIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";
import { useActiveJobs } from "../jobs/useJob";

export function RepoListPage() {
  const { data: repos, isPending, error } = useQuery({ queryKey: keys.repos, queryFn: api.repos });
  const active = useActiveJobs();

  if (isPending) return <Loading label="Loading repositories…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  const indexing = new Set(
    active.filter((j) => j.type === "index").map((j) => (j.params.source as string) ?? j.repo_root),
  );

  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <Heading as="h1" style={{ fontSize: 28 }}>
          Repositories
        </Heading>
        <span className="spacer" />
        <Button as={Link} to="/repos/new" variant="primary" leadingVisual={PlusIcon}>
          Add repository
        </Button>
      </div>
      {repos.length === 0 ? (
        <EmptyState
          icon={<RepoIcon size={24} />}
          title="No repositories indexed yet"
          body={
            <>
              An indexed repository is the starting point for everything here — point
              entrygraph at a git URL or a local checkout and it builds the graph.
            </>
          }
          action={
            <Button as={Link} to="/repos/new" variant="primary">
              Add your first repository
            </Button>
          }
        />
      ) : (
        <div className="card">
          {repos.map((r, i) => (
            <div
              key={r.id}
              className="row"
              style={{
                padding: "12px 16px",
                borderTop: i ? "1px solid var(--border)" : undefined,
              }}
            >
              <RepoIcon size={16} />
              <Link to={`/repos/${r.id}`} style={{ fontWeight: 600 }}>
                {r.name}
              </Link>
              {(indexing.has(r.root_path) || indexing.has(r.source?.url ?? "")) && (
                <Label size="small" variant="accent">
                  indexing…
                </Label>
              )}
              {r.source?.url && (
                <span className="muted fs0 clip" title={r.source.url}>
                  {r.source.url}
                </span>
              )}
              <span className="spacer" />
              <span className="muted fs0">
                {r.symbols.toLocaleString()} symbols · {r.files.toLocaleString()} files
              </span>
              <span className="muted fs0 mono clip" title={r.root_path} style={{ maxWidth: 280 }}>
                {r.root_path}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
