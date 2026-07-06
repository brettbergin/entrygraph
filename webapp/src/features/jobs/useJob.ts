// Poll a job at 1.5s until it reaches a terminal status; on success,
// invalidate the repo's query tree so stats/symbols/graph refetch.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, isTerminal, keys } from "../../api/queries";
import type { Job } from "../../api/types";

const POLL_MS = 1500;

export function useJob(jobId: string | null | undefined): Job | undefined {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: keys.job(jobId ?? "none"),
    queryFn: () => api.job(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (q) => (isTerminal(q.state.data?.status) ? false : POLL_MS),
  });

  // fire cache invalidation exactly once per terminal transition
  const settled = useRef<string | null>(null);
  useEffect(() => {
    const job = query.data;
    if (!job || !isTerminal(job.status) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: keys.repos });
    void queryClient.invalidateQueries({ queryKey: keys.activeJobs });
    if (job.repo_id != null) {
      void queryClient.invalidateQueries({ queryKey: keys.repo(job.repo_id) });
    }
  }, [query.data, queryClient]);

  return query.data;
}

/** Poll the set of queued/running jobs (drives the global toaster + badges). */
export function useActiveJobs(): Job[] {
  const { data } = useQuery({
    queryKey: keys.activeJobs,
    queryFn: async () => {
      const [queued, running] = await Promise.all([
        api.jobs({ status: "queued" }),
        api.jobs({ status: "running" }),
      ]);
      return [...running, ...queued];
    },
    refetchInterval: (q) => ((q.state.data?.length ?? 0) > 0 ? POLL_MS : 10_000),
  });
  return data ?? [];
}
