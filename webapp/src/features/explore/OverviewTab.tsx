import { Label, ProgressBar, Text } from "@primer/react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, keys } from "../../api/queries";
import { ErrorFlash, Loading } from "../../components/ui";
import { useRepoId } from "./RepoLayout";

export function OverviewTab() {
  const repoId = useRepoId();
  const stats = useQuery({ queryKey: keys.stats(repoId), queryFn: () => api.stats(repoId) });
  const detect = useQuery({ queryKey: keys.detect(repoId), queryFn: () => api.detect(repoId) });

  if (stats.isPending || detect.isPending) return <Loading label="Loading overview…" />;
  if (stats.error) return <ErrorFlash message={String(stats.error)} />;
  if (detect.error) return <ErrorFlash message={String(detect.error)} />;

  const s = stats.data;
  const tiles: Array<[string, number, string?]> = [
    ["Symbols", s.symbols],
    ["Call edges", s.edges, `${s.resolved_edges.toLocaleString()} resolved`],
    ["Entrypoints", s.entrypoints, "HTTP routes, CLI commands, tasks…"],
    ["Files", s.files],
    ["Sink edges", s.sink_edges, "calls into dangerous APIs"],
    ["Source edges", s.source_edges, "reads of untrusted input"],
  ];

  return (
    <>
      <div className="stats">
        {tiles.map(([label, n, hint]) => (
          <div key={label} className="card stat" title={hint}>
            <div className="n">{n.toLocaleString()}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>

      <div className="section split" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card" style={{ padding: 16 }}>
          <Text style={{ fontWeight: 600 }}>Languages</Text>
          {detect.data.languages.map((lang) => (
            <div key={lang.name} className="row" style={{ marginTop: 8 }}>
              <span style={{ width: 110 }}>{lang.name}</span>
              <span style={{ flex: 1 }}>
                <ProgressBar progress={lang.percent} aria-label={lang.name} />
              </span>
              <span className="muted fs0" style={{ width: 90, textAlign: "right" }}>
                {lang.percent.toFixed(1)}% · {lang.files} files
              </span>
            </div>
          ))}
        </div>
        <div className="card" style={{ padding: 16 }}>
          <Text style={{ fontWeight: 600 }}>Frameworks</Text>
          <div className="row wrap" style={{ marginTop: 8 }}>
            {detect.data.frameworks.length === 0 && (
              <span className="muted fs0">none detected</span>
            )}
            {detect.data.frameworks.map((fw) => (
              <Label key={fw.name} title={`confidence ${fw.confidence} — ${fw.evidence.join(", ")}`}>
                {fw.name}
                <span className="muted"> · {Math.round(fw.confidence * 100)}%</span>
              </Label>
            ))}
          </div>
          <Text as="p" className="muted fs0" style={{ marginTop: 16 }}>
            Framework detection drives entrypoint discovery — a Flask detection is
            what makes <code>@app.route</code> handlers show up under{" "}
            <Link to={`/repos/${repoId}/entrypoints`}>Entrypoints</Link>.
          </Text>
        </div>
      </div>
    </>
  );
}
