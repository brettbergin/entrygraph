// Typed client over the explorer read API. No auth — it's a read-only local tool.

import type {
  Entrypoint,
  Neighborhood,
  Path,
  Repo,
  StatsResponse,
  Symbol,
  SymbolDetail,
} from "./types";

export class ApiError extends Error {}

async function get<T>(path: string, params?: Record<string, string | number | boolean>): Promise<T> {
  const qs = params
    ? "?" +
      Object.entries(params)
        .filter(([, v]) => v !== "" && v !== undefined && v !== null)
        .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  const res = await fetch(`/api${path}${qs}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON */
    }
    throw new ApiError(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  repos: () => get<{ repos: Repo[] }>("/repos").then((r) => r.repos),

  stats: (rid: number) => get<StatsResponse>(`/repos/${rid}/stats`),

  symbols: (rid: number, params: { q?: string; kind?: string; file?: string; limit?: number }) =>
    get<{ symbols: Symbol[] }>(`/repos/${rid}/symbols`, params).then((r) => r.symbols),

  entrypoints: (rid: number, params: { framework?: string; kind?: string } = {}) =>
    get<{ entrypoints: Entrypoint[] }>(`/repos/${rid}/entrypoints`, params).then(
      (r) => r.entrypoints,
    ),

  symbol: (rid: number, qname: string) =>
    get<SymbolDetail>(`/repos/${rid}/symbol`, { qname }),

  graph: (rid: number, qname: string) => get<Neighborhood>(`/repos/${rid}/graph`, { qname }),

  paths: (
    rid: number,
    params: { source_category?: string; sink_category?: string; include_unresolved?: boolean },
  ) => get<{ paths: Path[]; mode: string | null }>(`/repos/${rid}/paths`, params),
};
