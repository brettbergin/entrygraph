// Query-key factory + fetchers for TanStack Query. Keys nest by repo so an
// index completion can invalidate ['repo', id] and cascade every view.

import { qs, request } from "./client";
import type {
  Detection,
  Entrypoint,
  Job,
  Me,
  Neighborhood,
  PathsQuery,
  PathsResponse,
  RegisterRepoRequest,
  Repo,
  Stats,
  SymbolDetail,
  Symbol as Sym,
} from "./types";

const V1 = "/api/v1";

export const keys = {
  me: ["me"] as const,
  repos: ["repos"] as const,
  repo: (id: number) => ["repo", id] as const,
  stats: (id: number) => ["repo", id, "stats"] as const,
  detect: (id: number) => ["repo", id, "detect"] as const,
  symbols: (id: number, params: Record<string, string>) =>
    ["repo", id, "symbols", params] as const,
  symbolDetail: (id: number, qname: string) => ["repo", id, "symbol", qname] as const,
  entrypoints: (id: number, params: Record<string, string>) =>
    ["repo", id, "entrypoints", params] as const,
  neighborhood: (id: number, qname: string) => ["repo", id, "graph", qname] as const,
  paths: (id: number, params: PathsQuery) => ["repo", id, "paths", params] as const,
  jobs: (params: Record<string, string>) => ["jobs", params] as const,
  job: (id: string) => ["job", id] as const,
  activeJobs: ["jobs", "active"] as const,
};

export const api = {
  me: () => request<Me>(`${V1}/me`),
  repos: () => request<{ repos: Repo[] }>(`${V1}/repos`).then((r) => r.repos),
  repo: (id: number) => request<{ repo: Repo }>(`${V1}/repos/${id}`).then((r) => r.repo),
  stats: (id: number) => request<{ stats: Stats }>(`${V1}/repos/${id}/stats`).then((r) => r.stats),
  detect: (id: number) => request<Detection>(`${V1}/repos/${id}/detect`),
  symbols: (id: number, params: Record<string, string>) =>
    request<{ symbols: Sym[] }>(`${V1}/repos/${id}/symbols${qs(params)}`).then((r) => r.symbols),
  symbolDetail: (id: number, qname: string) =>
    request<SymbolDetail>(`${V1}/repos/${id}/symbol${qs({ qname })}`),
  entrypoints: (id: number, params: Record<string, string>) =>
    request<{ entrypoints: Entrypoint[] }>(`${V1}/repos/${id}/entrypoints${qs(params)}`).then(
      (r) => r.entrypoints,
    ),
  neighborhood: (id: number, qname: string) =>
    request<Neighborhood>(`${V1}/repos/${id}/graph${qs({ qname })}`),
  paths: (id: number, params: PathsQuery) =>
    request<PathsResponse>(
      `${V1}/repos/${id}/paths${qs(params as Record<string, string | number | boolean | undefined>)}`,
    ),
  logout: () => request<unknown>("/auth/logout", { method: "POST" }),
  registerRepo: (body: RegisterRepoRequest) =>
    request<{ job_id: string }>(`${V1}/repos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  reindexRepo: (id: number, body: { full?: boolean }) =>
    request<{ job_id: string }>(`${V1}/repos/${id}/index`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteRepo: (id: number) =>
    request<{ deleted: string }>(`${V1}/repos/${id}`, { method: "DELETE" }),
  jobs: (params: Record<string, string>) =>
    request<{ jobs: Job[] }>(`${V1}/jobs${qs(params)}`).then((r) => r.jobs),
  job: (id: string) => request<{ job: Job }>(`${V1}/jobs/${id}`).then((r) => r.job),
  cancelJob: (id: string) =>
    request<{ status: string }>(`${V1}/jobs/${id}/cancel`, { method: "POST" }),
};

export function isTerminal(status: Job["status"] | undefined): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}
