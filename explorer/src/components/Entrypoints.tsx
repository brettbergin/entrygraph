import { api } from "../api";
import type { Entrypoint } from "../types";
import { Empty, ErrorFlash, KindLabel, Loading, loc, useAsync } from "./ui";

export function Entrypoints({
  repoId,
  onOpenGraph,
}: {
  repoId: number;
  onOpenGraph: (qname: string) => void;
}) {
  const { data, error, loading } = useAsync<Entrypoint[]>(
    () => api.entrypoints(repoId),
    [repoId],
  );
  if (error) return <ErrorFlash message={error} />;
  if (loading) return <Loading label="Loading entrypoints…" />;
  if (!data || data.length === 0)
    return <Empty>No entrypoints detected (routes, CLI commands, handlers, tasks).</Empty>;

  return (
    <div className="card section" style={{ overflow: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--fg-muted)" }}>
            <th style={{ padding: "10px 14px", fontSize: 11 }}>KIND</th>
            <th style={{ padding: "10px 14px", fontSize: 11 }}>FRAMEWORK</th>
            <th style={{ padding: "10px 14px", fontSize: 11 }}>ROUTE / NAME</th>
            <th style={{ padding: "10px 14px", fontSize: 11 }}>HANDLER</th>
          </tr>
        </thead>
        <tbody>
          {data.map((e) => (
            <tr key={e.id} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "9px 14px" }}>
                <KindLabel kind={e.kind} />
              </td>
              <td style={{ padding: "9px 14px" }} className="muted">
                {e.framework ?? "—"}
              </td>
              <td style={{ padding: "9px 14px" }} className="mono">
                {e.http_method ? <span className="accent">{e.http_method} </span> : null}
                {e.route ?? e.handler?.name ?? "—"}
              </td>
              <td style={{ padding: "9px 14px" }}>
                {e.handler ? (
                  <button
                    className="rowbtn mono clip"
                    style={{ maxWidth: 380 }}
                    onClick={() => onOpenGraph(e.handler!.qname)}
                  >
                    {e.handler.qname}
                  </button>
                ) : (
                  <span className="muted">—</span>
                )}
                {e.handler && (
                  <div className="muted fs0">{loc(e.handler.file, e.handler.line)}</div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
