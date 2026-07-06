import { Label, ProgressBar } from "@primer/react";
import type { Job, JobStatus } from "../../api/types";

const STATUS_VARIANT: Record<JobStatus, "secondary" | "accent" | "success" | "danger" | "attention"> = {
  queued: "secondary",
  running: "accent",
  succeeded: "success",
  failed: "danger",
  cancelled: "attention",
};

export function JobStatusLabel({ status }: { status: JobStatus }) {
  return (
    <Label size="small" variant={STATUS_VARIANT[status]}>
      {status}
    </Label>
  );
}

export function JobProgress({ job }: { job: Job }) {
  if (job.status !== "running") return <JobStatusLabel status={job.status} />;
  return (
    <span className="row" style={{ gap: 8, minWidth: 220 }}>
      <span style={{ flex: 1 }}>
        <ProgressBar progress={job.progress * 100} aria-label="job progress" />
      </span>
      <span className="muted fs0">{job.message ?? job.phase ?? "running"}</span>
    </span>
  );
}
