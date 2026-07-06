import { Heading, Label } from "@primer/react";
import { ShieldCheckIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";

export function SentinelPage() {
  const { data, isPending, error } = useQuery({
    queryKey: keys.installations,
    queryFn: api.installations,
  });

  if (isPending) return <Loading label="Loading installations…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  return (
    <>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 8 }}>
        Sentinel
      </Heading>
      <p className="muted" style={{ marginTop: 0 }}>
        Sentinel is the GitHub App that runs the reachability gate on pull requests. These are its
        installations and the repositories it watches.
      </p>
      {data.length === 0 ? (
        <EmptyState
          icon={<ShieldCheckIcon size={24} />}
          title="No installations"
          body="Install the Sentinel GitHub App on an organization or repository to see it here."
        />
      ) : (
        <div className="card">
          {data.map((inst, i) => (
            <Link
              key={inst.id}
              to={`/sentinel/installations/${inst.id}`}
              className="row"
              style={{
                padding: "12px 16px",
                borderTop: i ? "1px solid var(--border)" : undefined,
                textDecoration: "none",
                color: "inherit",
              }}
            >
              <ShieldCheckIcon size={16} />
              <span style={{ fontWeight: 600 }}>{inst.account_login}</span>
              {inst.suspended && <Label variant="attention">suspended</Label>}
              <span className="spacer" />
              <span className="muted fs0">{inst.repo_count} repos</span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

import { useParams } from "react-router";

export function InstallationPage() {
  const { instId } = useParams();
  const id = Number(instId);
  const { data, isPending, error } = useQuery({
    queryKey: keys.installationRepos(id),
    queryFn: () => api.installationRepos(id),
  });

  if (isPending) return <Loading />;
  if (error) return <ErrorFlash message={String(error)} />;

  return (
    <>
      <Heading as="h1" style={{ fontSize: 24, marginBottom: 16 }}>
        Installation #{id} repositories
      </Heading>
      {data.length === 0 ? (
        <EmptyState title="No repositories" body="This installation watches no repositories yet." />
      ) : (
        <div className="card">
          {data.map((r, i) => (
            <div
              key={r.repo_id}
              className="row"
              style={{ padding: "10px 16px", borderTop: i ? "1px solid var(--border)" : undefined }}
            >
              <span className="mono">{r.full_name}</span>
              <span className="spacer" />
              <Link className="fs0" to={`/repos/${r.repo_id}/security`}>
                view security
              </Link>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
