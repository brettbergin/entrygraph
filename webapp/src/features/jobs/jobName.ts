import type { Job } from "../../api/types";

/** A human name for the job's target repo, from its params or result. */
export function repoNameFromParams(job: Job): string {
  const source = (job.params.source as string) ?? job.repo_root ?? "repository";
  return source.replace(/\.git$/, "").replace(/\/+$/, "").split("/").pop() || source;
}
