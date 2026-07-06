import { Button, Heading } from "@primer/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";
import { JobProgress, JobStatusLabel } from "./JobBadges";
import { repoNameFromParams } from "./jobName";
import { useJob } from "./useJob";

export function JobsPage() {
  const { data, isPending, error } = useQuery({
    queryKey: keys.jobs({}),
    queryFn: () => api.jobs({}),
    refetchInterval: 3000,
  });

  if (isPending) return <Loading label="Loading jobs…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  return (
    <>
      <Heading as="h1" style={{ fontSize: 28, marginBottom: 16 }}>
        Jobs
      </Heading>
      {data.length === 0 ? (
        <EmptyState
          title="No jobs yet"
          body="Jobs appear when you add a repository or trigger a re-index. Each one clones (if needed), parses, and writes the graph."
        />
      ) : (
        <div className="card">
          {data.map((j, i) => (
            <div
              key={j.id}
              className="row"
              style={{ padding: "10px 16px", borderTop: i ? "1px solid var(--border)" : undefined }}
            >
              <JobStatusLabel status={j.status} />
              <Link to={`/jobs/${j.id}`} style={{ fontWeight: 600 }}>
                {j.type === "index" ? `index ${repoNameFromParams(j)}` : j.type}
              </Link>
              {j.status === "running" && <JobProgress job={j} />}
              <span className="spacer" />
              {j.stats && (
                <span className="muted fs0">
                  {j.stats.symbols.toLocaleString()} symbols · {j.stats.duration_seconds}s
                </span>
              )}
              <span className="muted fs0">{j.created_at?.slice(0, 19).replace("T", " ")}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

export function JobDetailPage() {
  const { jobId } = useParams();
  const job = useJob(jobId);
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api.cancelJob(jobId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.job(jobId!) }),
  });

  if (!job) return <Loading label="Loading job…" />;

  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <Heading as="h1" style={{ fontSize: 24 }}>
          {job.type === "index" ? `Index ${repoNameFromParams(job)}` : job.type}
        </Heading>
        <JobStatusLabel status={job.status} />
        <span className="spacer" />
        {(job.status === "running" || job.status === "queued") && (
          <Button
            variant="danger"
            size="small"
            disabled={job.cancel_requested || cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            {job.cancel_requested ? "Cancelling…" : "Cancel"}
          </Button>
        )}
      </div>

      <div className="card" style={{ padding: 16 }}>
        {job.status === "running" && <JobProgress job={job} />}
        {job.error && (
          <pre className="mono fs0 danger" style={{ whiteSpace: "pre-wrap" }}>
            {job.error}
          </pre>
        )}
        {job.stats && (
          <div className="stats" style={{ marginTop: 12 }}>
            {(
              [
                ["Symbols", job.stats.symbols],
                ["Edges", job.stats.edges],
                ["Entrypoints", job.stats.entrypoints],
                ["Files indexed", job.stats.files_indexed],
                ["Duration", `${job.stats.duration_seconds}s`],
              ] as Array<[string, number | string]>
            ).map(([l, n]) => (
              <div key={l} className="card stat">
                <div className="n">{typeof n === "number" ? n.toLocaleString() : n}</div>
                <div className="l">{l}</div>
              </div>
            ))}
          </div>
        )}
        <div className="muted fs0" style={{ marginTop: 12 }}>
          {job.created_by && <>started by {job.created_by} · </>}
          created {job.created_at?.slice(0, 19).replace("T", " ")}
          {job.finished_at && <> · finished {job.finished_at.slice(0, 19).replace("T", " ")}</>}
        </div>
        {job.status === "succeeded" && job.repo_id != null && (
          <div style={{ marginTop: 12 }}>
            <Button as={Link} to={`/repos/${job.repo_id}`} variant="primary">
              Explore repository
            </Button>
          </div>
        )}
      </div>
    </>
  );
}
