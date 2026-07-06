import { Button, FormControl, Select, Flash } from "@primer/react";
import { ShieldCheckIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api, keys } from "../../../api/queries";
import { EmptyState } from "../../../components/EmptyState";
import { ErrorFlash, Loading } from "../../../components/ui";
import { useRepoId } from "../RepoLayout";
import { PRESETS, SINK_CATEGORIES, SOURCE_CATEGORIES } from "./categories";
import { PathList } from "./PathList";
import type { PathsQuery } from "../../../api/types";

export function ReachabilityTab() {
  const repoId = useRepoId();
  const [params, setParams] = useSearchParams();
  const source = params.get("source_category") ?? "";
  const sink = params.get("sink_category") ?? "";
  const ran = Boolean(source && sink);

  const query: PathsQuery = { source_category: source, sink_category: sink, max_paths: 50 };
  const { data, isFetching, error } = useQuery({
    queryKey: keys.paths(repoId, query),
    queryFn: () => api.paths(repoId, query),
    enabled: ran,
  });

  const run = (src: string, snk: string) =>
    setParams(
      (p) => {
        p.set("source_category", src);
        p.set("sink_category", snk);
        return p;
      },
      { replace: true },
    );

  const sourceInfo = SOURCE_CATEGORIES.find((c) => c.id === source);
  const sinkInfo = SINK_CATEGORIES.find((c) => c.id === sink);

  return (
    <>
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="row wrap" style={{ alignItems: "end" }}>
          <FormControl>
            <FormControl.Label>Where can data come from?</FormControl.Label>
            <FormControl.Caption>{sourceInfo?.description ?? "Pick a source of untrusted input."}</FormControl.Caption>
            <Select value={source} onChange={(e) => e.target.value && run(e.target.value, sink || "all")}>
              <Select.Option value="">choose a source…</Select.Option>
              {SOURCE_CATEGORIES.map((c) => (
                <Select.Option key={c.id} value={c.id}>
                  {c.title}
                </Select.Option>
              ))}
            </Select>
          </FormControl>
          <span className="arrow" style={{ paddingBottom: 26 }}>
            →
          </span>
          <FormControl>
            <FormControl.Label>What are you worried about it reaching?</FormControl.Label>
            <FormControl.Caption>{sinkInfo?.description ?? "Pick a dangerous operation."}</FormControl.Caption>
            <Select value={sink} onChange={(e) => e.target.value && run(source || "http_input", e.target.value)}>
              <Select.Option value="">choose a sink…</Select.Option>
              {SINK_CATEGORIES.map((c) => (
                <Select.Option key={c.id} value={c.id}>
                  {c.title}
                </Select.Option>
              ))}
            </Select>
          </FormControl>
        </div>
        <div className="row wrap" style={{ marginTop: 12 }}>
          <span className="muted fs0">Try:</span>
          {PRESETS.map((p) => (
            <Button key={p.label} size="small" onClick={() => run(p.source, p.sink)}>
              {p.label}
            </Button>
          ))}
        </div>
      </div>

      {!ran ? (
        <EmptyState
          icon={<ShieldCheckIcon size={24} />}
          title="Ask a reachability question"
          body={
            <>
              A <b>reachability path</b> is a chain of calls from a <b>source</b> (where
              untrusted input enters) to a <b>sink</b> (a dangerous API). Paths are
              risk-ranked; sanitizers on the way discount the score. Pick a source and a
              sink above, or start from a preset.
            </>
          }
        />
      ) : isFetching ? (
        <Loading label="Enumerating paths…" />
      ) : error ? (
        <ErrorFlash message={String(error)} />
      ) : !data ? null : (
        <>
          {data.mode === "widened" && (
            <Flash style={{ marginBottom: 16 }}>
              No high-confidence paths were found, so the search widened to speculative
              edges (class-hierarchy dispatch, unresolved calls). Treat these as leads to
              verify, not confirmed flows.
            </Flash>
          )}
          {data.truncated && (
            <Flash variant="warning" style={{ marginBottom: 16 }}>
              The search budget was spent before finishing — an empty or short result may
              mean “ran out of budget”, not “safe”.
            </Flash>
          )}
          {data.paths.length === 0 ? (
            <EmptyState
              title="No paths found"
              body={
                <>
                  No call chain connects {sourceInfo?.title.toLowerCase() ?? source} to{" "}
                  {sinkInfo?.title.toLowerCase() ?? sink} in this index. That's good news —
                  but remember reachability is call-graph based: dynamic dispatch the
                  indexer couldn't resolve may hide paths.
                </>
              }
            />
          ) : (
            <>
              <div className="muted fs0" style={{ marginBottom: 10 }}>
                {data.paths.length} path{data.paths.length === 1 ? "" : "s"} · risk-ranked,
                highest first · mode: {data.mode}
              </div>
              <PathList paths={data.paths} />
            </>
          )}
        </>
      )}
    </>
  );
}
