// A single reachability path as a vertical flow: source frame → hops (with
// file:line and edge-kind chips) → sink frame. Linear paths read better as a
// timeline than as a force graph.

import { Label } from "@primer/react";
import { Link } from "react-router";
import { InfoPopover } from "../../../components/InfoPopover";
import { ConfidenceBadge } from "../../../components/ui";
import { useRepoId } from "../RepoLayout";
import type { CallPath } from "../../../api/types";

const SEVERITY_VARIANT: Record<string, "danger" | "attention" | "secondary"> = {
  critical: "danger",
  high: "danger",
  medium: "attention",
  low: "secondary",
};

function SeverityBadge({ severity }: { severity: string | null }) {
  if (!severity) return null;
  const variant = SEVERITY_VARIANT[severity] ?? "secondary";
  return (
    <Label size="small" variant={variant} title={`severity: ${severity}`}>
      {severity}
    </Label>
  );
}

export function PathFlow({ path, index }: { path: CallPath; index: number }) {
  const repoId = useRepoId();
  const source = path.hops[0];
  const sink = path.hops[path.hops.length - 1];

  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <span className="muted fs0">#{index + 1}</span>
        <SeverityBadge severity={path.severity} />
        <ConfidenceBadge confidence={path.min_confidence} />
        {path.verified === true && (
          <span className="row" style={{ gap: 2 }}>
            <Label size="small" variant="success">flow confirmed</Label>
            <InfoPopover term="taint_verified" />
          </span>
        )}
        {path.verified === false && (
          <Label size="small" variant="secondary">not observed</Label>
        )}
        <span className="spacer" />
        <Link className="fs0" to={`/repos/${repoId}/graph?focus=${encodeURIComponent(sink.qname)}`}>
          view on graph
        </Link>
      </div>

      {/* source frame */}
      <div className="hop source" style={{ display: "inline-block" }}>
        SOURCE · {path.source_channel ?? path.source_category ?? "input"}
        {path.source_key ? `: ${path.source_key}` : ""}
      </div>
      {path.source_snippet && (
        <pre className="mono fs0 muted" style={{ margin: "4px 0 0 12px", whiteSpace: "pre-wrap" }}>
          {source.file}: {path.source_snippet}
        </pre>
      )}

      {/* hops */}
      <div style={{ borderLeft: "2px solid var(--border)", margin: "8px 0 8px 10px", paddingLeft: 16 }}>
        {path.hops.map((hop, i) => {
          const edge = i > 0 ? path.edges[i - 1] : null;
          return (
            <div key={`${hop.qname}-${i}`} style={{ padding: "4px 0" }}>
              <span className="mono fs0">{hop.qname}</span>
              {hop.file && (
                <span className="muted fs0">
                  {" "}
                  · {hop.file}
                  {edge ? `:${edge.line}` : ""}
                </span>
              )}
              {edge?.via && (
                <Label size="small" variant="secondary" sx-none="">
                  {edge.via}
                </Label>
              )}
            </div>
          );
        })}
      </div>

      {/* sink frame */}
      <div className="hop sink" style={{ display: "inline-block" }}>
        SINK · {path.sink_id ?? sink.qname}
      </div>
      {path.sink_snippet && (
        <pre className="mono fs0 muted" style={{ margin: "4px 0 0 12px", whiteSpace: "pre-wrap" }}>
          {sink.file}: {path.sink_snippet}
        </pre>
      )}
    </div>
  );
}
