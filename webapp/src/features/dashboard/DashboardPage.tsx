import { Button, Heading, Text, Label } from "@primer/react";
import { RepoIcon, SearchIcon, ShieldCheckIcon, TelescopeIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";

function FirstRun() {
  return (
    <EmptyState
      icon={<TelescopeIcon size={24} />}
      title="Map your code as a graph"
      body={
        <>
          entrygraph indexes a repository into a queryable graph of{" "}
          <b>symbols</b> (functions, classes), <b>call edges</b>, and{" "}
          <b>entrypoints</b> (HTTP routes, CLI commands) — then answers questions
          like <i>“can any HTTP route reach a shell command?”</i>
          <br />
          <br />
          <span className="muted">
            1. <RepoIcon size={14} /> Index a repository &nbsp; 2. <SearchIcon size={14} /> Explore
            symbols &amp; entrypoints &nbsp; 3. <ShieldCheckIcon size={14} /> Check source→sink
            reachability
          </span>
        </>
      }
      action={
        <Button as={Link} to="/repos/new" variant="primary" size="large">
          Add your first repository
        </Button>
      }
    />
  );
}

export function DashboardPage() {
  const { data: repos, isPending, error } = useQuery({ queryKey: keys.repos, queryFn: api.repos });

  if (isPending) return <Loading label="Loading…" />;
  if (error) return <ErrorFlash message={String(error)} />;
  if (!repos.length) return <FirstRun />;

  return (
    <>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 16 }}>
        Dashboard
      </Heading>
      <div className="stats">
        {repos.map((r) => (
          <Link key={r.id} to={`/repos/${r.id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <div className="card stat">
              <div className="row">
                <RepoIcon size={16} />
                <Text className="clip" style={{ fontWeight: 600 }}>
                  {r.name}
                </Text>
                {r.sentinel && <Label size="small">sentinel</Label>}
              </div>
              <div className="n">{r.symbols.toLocaleString()}</div>
              <div className="l">symbols · {r.files.toLocaleString()} files</div>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
