// Bottom-right toasts for active jobs (mounted once in the shell). Click
// through to the jobs page for detail. Primer has no toast primitive — this
// stays deliberately dumb: newest 3, no queueing.

import { ProgressBar } from "@primer/react";
import { Link } from "react-router";
import { useActiveJobs } from "./useJob";
import { repoNameFromParams } from "./jobName";

export function JobToaster() {
  const active = useActiveJobs();
  if (!active.length) return null;
  return (
    <div
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 20,
        width: 320,
      }}
    >
      {active.slice(0, 3).map((job) => (
        <Link key={job.id} to={`/jobs/${job.id}`} style={{ textDecoration: "none", color: "inherit" }}>
          <div className="card" style={{ padding: "10px 14px" }}>
            <div className="row">
              <span className="clip" style={{ fontWeight: 600 }}>
                {job.type === "index" ? `Indexing ${repoNameFromParams(job)}` : job.type}
              </span>
              <span className="spacer" />
              <span className="muted fs0">{job.status}</span>
            </div>
            <div style={{ marginTop: 6 }}>
              <ProgressBar
                progress={job.status === "running" ? job.progress * 100 : 2}
                aria-label="progress"
              />
            </div>
            {job.message && <div className="muted fs0" style={{ marginTop: 4 }}>{job.message}</div>}
          </div>
        </Link>
      ))}
      {active.length > 3 && (
        <Link to="/jobs" className="muted fs0" style={{ textAlign: "right" }}>
          +{active.length - 3} more jobs
        </Link>
      )}
    </div>
  );
}
