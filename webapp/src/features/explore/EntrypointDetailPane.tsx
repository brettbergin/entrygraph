// Detail pane for one entrypoint: the route with its path params highlighted,
// the parameter table (name/location/required/type/provenance), and the
// per-parameter data flows rendered with the same PathFlow timeline the
// Reachability tab uses.

import { Label, Text } from "@primer/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { InfoPopover } from "../../components/InfoPopover";
import { ErrorFlash, Loading } from "../../components/ui";
import { PathFlow } from "./reachability/PathFlow";
import { useRepoId } from "./RepoLayout";
import type { CallPath, Entrypoint, Parameter } from "../../api/types";

const PROVENANCE_LABEL: Record<string, string> = {
  route: "route template",
  dsl: "params block",
  strong_params: "strong params",
  usage: "observed read",
};

/** Render a route template with its parameter segments highlighted. */
function RouteTemplate({ route }: { route: string }) {
  const parts = route.split(/([:*][A-Za-z_]\w*)/g);
  return (
    <span className="mono" style={{ fontWeight: 600, wordBreak: "break-all" }}>
      {parts.map((part, i) =>
        /^[:*]/.test(part) ? (
          <span key={i} style={{ color: "var(--accent, #0969da)" }}>
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </span>
  );
}

function ParameterTable({ parameters }: { parameters: Parameter[] }) {
  return (
    <table className="fs0" style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr className="muted" style={{ textAlign: "left" }}>
          <th style={{ padding: "4px 8px 4px 0" }}>name</th>
          <th style={{ padding: "4px 8px" }}>in</th>
          <th style={{ padding: "4px 8px" }}>required</th>
          <th style={{ padding: "4px 8px" }}>type</th>
          <th style={{ padding: "4px 8px" }}>from</th>
        </tr>
      </thead>
      <tbody>
        {parameters.map((p) => (
          <tr key={`${p.name}-${p.location}`} style={{ borderTop: "1px solid var(--border)" }}>
            <td className="mono" style={{ padding: "4px 8px 4px 0" }}>
              {p.name}
            </td>
            <td style={{ padding: "4px 8px" }}>
              <Label size="small" variant={p.location === "path" ? "accent" : "secondary"}>
                {p.location}
              </Label>
            </td>
            <td style={{ padding: "4px 8px" }}>{p.required ? "yes" : "no"}</td>
            <td className="mono" style={{ padding: "4px 8px" }}>
              {p.type ?? "—"}
            </td>
            <td className="muted" style={{ padding: "4px 8px" }}>
              {PROVENANCE_LABEL[p.provenance] ?? p.provenance}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FlowsSection({ entrypoint }: { entrypoint: Entrypoint }) {
  const repoId = useRepoId();
  const { data, isPending, error } = useQuery({
    queryKey: keys.entrypointFlows(repoId, entrypoint.id),
    queryFn: () => api.entrypointFlows(repoId, entrypoint.id),
  });

  if (isPending) return <Loading label="Tracing data flow…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  const attributed = data.parameters.filter((pf) => pf.paths.length > 0);
  const total =
    attributed.reduce((n, pf) => n + pf.paths.length, 0) + data.unmatched_paths.length;

  if (total === 0) {
    return (
      <div className="muted fs0">
        No sink-reaching flows found from this handler.{" "}
        {entrypoint.handler && (
          <Link
            to={`/repos/${repoId}/graph?focus=${encodeURIComponent(entrypoint.handler.qname)}`}
          >
            Explore its call graph
          </Link>
        )}
      </div>
    );
  }

  const renderPaths = (paths: CallPath[]) =>
    paths.map((p, i) => <PathFlow key={i} path={p} index={i} />);

  return (
    <>
      {data.truncated && (
        <div className="muted fs0" style={{ marginBottom: 8 }}>
          Search budget hit — more flows may exist.
        </div>
      )}
      {attributed.map((pf) => (
        <div key={`${pf.parameter.name}-${pf.parameter.location}`} className="section">
          <Text className="fs0" style={{ fontWeight: 600 }}>
            <span className="mono">{pf.parameter.name}</span>{" "}
            <span className="muted">({pf.parameter.location})</span>
          </Text>
          {renderPaths(pf.paths)}
        </div>
      ))}
      {data.unmatched_paths.length > 0 && (
        <div className="section">
          <Text className="fs0 muted" style={{ fontWeight: 600 }}>
            Other flows from this handler
          </Text>
          {renderPaths(data.unmatched_paths)}
        </div>
      )}
    </>
  );
}

export function EntrypointDetailPane({ entrypoint }: { entrypoint: Entrypoint }) {
  const repoId = useRepoId();
  return (
    <aside className="card detail">
      <div className="row wrap">
        {entrypoint.http_method && (
          <Label size="small" variant="accent">
            {entrypoint.http_method}
          </Label>
        )}
        {entrypoint.route ? <RouteTemplate route={entrypoint.route} /> : <span>—</span>}
      </div>
      <div className="muted fs0" style={{ marginTop: 4 }}>
        {entrypoint.kind}
        {entrypoint.framework && <> · {entrypoint.framework}</>}
      </div>
      {entrypoint.handler && (
        <div className="clip" style={{ marginTop: 4 }}>
          <Link
            className="mono fs0"
            to={`/repos/${repoId}/symbols?sel=${encodeURIComponent(entrypoint.handler.qname)}`}
          >
            {entrypoint.handler.qname}
          </Link>
        </div>
      )}

      <div className="section">
        <span className="row" style={{ gap: 2 }}>
          <Text className="muted fs0" style={{ fontWeight: 600 }}>
            Parameters ({entrypoint.parameters.length})
          </Text>
          <InfoPopover term="parameter" />
        </span>
        {entrypoint.parameters.length === 0 ? (
          <div className="muted fs0">
            None recorded — either the route takes no input, its language/framework has no
            parameter support yet, or this repo hasn't been re-indexed since parameters landed.
          </div>
        ) : (
          <ParameterTable parameters={entrypoint.parameters} />
        )}
      </div>

      <div className="section">
        <span className="row" style={{ gap: 2 }}>
          <Text className="muted fs0" style={{ fontWeight: 600 }}>
            Data flow
          </Text>
          <InfoPopover term="parameter_flows" />
        </span>
        <FlowsSection entrypoint={entrypoint} />
      </div>
    </aside>
  );
}
