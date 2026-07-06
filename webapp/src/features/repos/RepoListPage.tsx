import { Heading, Label } from "@primer/react";
import { RepoIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";

export function RepoListPage() {
  const { data: repos, isPending, error } = useQuery({ queryKey: keys.repos, queryFn: api.repos });

  if (isPending) return <Loading label="Loading repositories…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  return (
    <>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 16 }}>
        Repositories
      </Heading>
      {repos.length === 0 ? (
        <EmptyState
          icon={<RepoIcon size={24} />}
          title="No repositories indexed yet"
          body={
            <>
              An indexed repository is the starting point for everything here.
              Index one from a terminal:{" "}
              <code className="mono">entrygraph index &lt;path-or-git-url&gt;</code>
            </>
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
              {r.sentinel && <Label size="small">sentinel</Label>}
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
