import { Label } from "@primer/react";
import { Link } from "react-router";
import type { CallPath } from "../../../api/types";
import { ConfidenceBadge, RiskBadge } from "../../../components/ui";
import { useRepoId } from "../RepoLayout";

function PathCard({ path, index }: { path: CallPath; index: number }) {
  const repoId = useRepoId();
  const source = path.hops[0];
  const sink = path.hops[path.hops.length - 1];
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div className="row wrap">
        <span className="muted fs0">#{index + 1}</span>
        <RiskBadge risk={path.risk} />
        <ConfidenceBadge confidence={path.min_confidence} />
        {path.verified === true && (
          <Label size="small" variant="success" title="a reaching-defs check confirmed the tainted value flows to the sink">
            taint verified
          </Label>
        )}
        {path.verified === false && (
          <Label size="small" variant="secondary" title="a reaching-defs check found no flow — demoted, likely a false positive">
            no flow proven
          </Label>
        )}
        {path.source_key && (
          <Label size="small" title={`the specific ${path.source_channel ?? "input"} read`}>
            {path.source_channel ?? "input"}: {path.source_key}
          </Label>
        )}
        {path.edges.some((e) => e.sanitized_by.length > 0) && (
          <Label size="small" variant="attention" title="a sanitizer for this sink category is called on or near this path — risk discounted, not eliminated">
            sanitizer seen
          </Label>
        )}
        <span className="spacer" />
        <Link
          className="fs0"
          to={`/repos/${repoId}/graph?focus=${encodeURIComponent(sink.qname)}`}
        >
          view on graph
        </Link>
      </div>

      <div className="section">
        <div className="row fs0">
          <span className="success" style={{ width: 60, fontWeight: 600 }}>
            SOURCE
          </span>
          <span className="mono clip">{source.qname}</span>
          <span className="muted mono fs0 clip">{source.file ?? ""}</span>
        </div>
        {path.source_snippet && (
          <pre className="mono fs0 muted" style={{ margin: "2px 0 0 60px", whiteSpace: "pre-wrap" }}>
            {path.source_snippet}
          </pre>
        )}
        <div className="chain" style={{ margin: "10px 0 10px 60px" }}>
          {path.hops.map((hop, i) => (
            <span key={`${hop.qname}-${i}`} className="row" style={{ gap: 6 }}>
              {i > 0 && <span className="arrow">→</span>}
              <span
                className={`hop ${i === 0 ? "source" : i === path.hops.length - 1 ? "sink" : ""}`}
                title={
                  i > 0
                    ? `line ${path.edges[i - 1].line}${path.edges[i - 1].via ? ` · via ${path.edges[i - 1].via}` : ""}`
                    : undefined
                }
              >
                {hop.name}
              </span>
            </span>
          ))}
        </div>
        <div className="row fs0">
          <span className="danger" style={{ width: 60, fontWeight: 600 }}>
            SINK
          </span>
          <span className="mono clip">{sink.qname}</span>
          {path.sink_id && <span className="muted fs0">{path.sink_id}</span>}
        </div>
        {path.sink_snippet && (
          <pre className="mono fs0 muted" style={{ margin: "2px 0 0 60px", whiteSpace: "pre-wrap" }}>
            {path.sink_snippet}
          </pre>
        )}
      </div>
    </div>
  );
}

export function PathList({ paths }: { paths: CallPath[] }) {
  return (
    <>
      {paths.map((p, i) => (
        <PathCard key={i} path={p} index={i} />
      ))}
    </>
  );
}
