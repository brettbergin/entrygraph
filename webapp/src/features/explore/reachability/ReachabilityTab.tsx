import { Button, Flash, FormControl, Select, ToggleSwitch } from "@primer/react";
import { ShieldCheckIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { useSearchParams } from "react-router";
import { api, keys } from "../../../api/queries";
import { EmptyState } from "../../../components/EmptyState";
import { InfoPopover } from "../../../components/InfoPopover";
import { ErrorFlash, Loading } from "../../../components/ui";
import { useRepoId } from "../RepoLayout";
import { AdvancedOptions } from "./AdvancedOptions";
import { PRESETS, SINK_CATEGORIES, SOURCE_CATEGORIES } from "./categories";
import { PathFlow } from "./PathFlow";
import { pathsCliEcho } from "./queryEcho";
import type { PathsQuery } from "../../../api/types";

const BOOL_FLAGS = [
  "strict",
  "include_fuzzy",
  "include_unresolved",
  "include_callbacks",
  "prune_sanitized",
  "explicit_sources",
  "confirmed_only",
] as const;

// Read the full query out of the URL so every view is deep-linkable/shareable.
function queryFromParams(params: URLSearchParams): PathsQuery {
  const q: PathsQuery = {
    source_category: params.get("source_category") ?? undefined,
    sink_category: params.get("sink_category") ?? undefined,
    source: params.get("source") ?? undefined,
    sink: params.get("sink") ?? undefined,
    min_confidence: (params.get("min_confidence") as PathsQuery["min_confidence"]) ?? undefined,
    max_paths: 50,
  };
  if (params.get("max_depth")) q.max_depth = Number(params.get("max_depth"));
  if (params.get("taint_hops")) q.taint_hops = Number(params.get("taint_hops"));
  for (const f of BOOL_FLAGS) if (params.get(f) === "1") q[f] = true;
  return q;
}

export function ReachabilityTab() {
  const repoId = useRepoId();
  const [params, setParams] = useSearchParams();
  const advanced = params.get("adv") === "1";
  const query = queryFromParams(params);
  const ran = Boolean(query.source_category || query.sink_category || query.source || query.sink);

  const { data, isFetching, error } = useQuery({
    queryKey: keys.paths(repoId, query),
    queryFn: () => api.paths(repoId, query),
    enabled: ran,
  });

  const patch = useCallback(
    (p: Partial<PathsQuery>) =>
      setParams(
        (sp) => {
          for (const [k, v] of Object.entries(p)) {
            if (v === undefined || v === false || v === "") sp.delete(k);
            else if (v === true) sp.set(k, "1");
            else sp.set(k, String(v));
          }
          return sp;
        },
        { replace: true },
      ),
    [setParams],
  );

  const runPreset = (src: string, snk: string) =>
    patch({ source_category: src, sink_category: snk, source: undefined, sink: undefined });

  const sourceInfo = SOURCE_CATEGORIES.find((c) => c.id === query.source_category);
  const sinkInfo = SINK_CATEGORIES.find((c) => c.id === query.sink_category);

  return (
    <>
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div className="row wrap" style={{ alignItems: "end" }}>
          <FormControl>
            <FormControl.Label>
              Where can data come from? <InfoPopover term="source" />
            </FormControl.Label>
            <FormControl.Caption>
              {sourceInfo?.description ?? "Pick a source of untrusted input."}
            </FormControl.Caption>
            <Select
              value={query.source_category ?? ""}
              onChange={(e) => patch({ source_category: e.target.value || undefined })}
            >
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
            <FormControl.Label>
              What are you worried about it reaching? <InfoPopover term="sink" />
            </FormControl.Label>
            <FormControl.Caption>
              {sinkInfo?.description ?? "Pick a dangerous operation."}
            </FormControl.Caption>
            <Select
              value={query.sink_category ?? ""}
              onChange={(e) => patch({ sink_category: e.target.value || undefined })}
            >
              <Select.Option value="">choose a sink…</Select.Option>
              {SINK_CATEGORIES.map((c) => (
                <Select.Option key={c.id} value={c.id}>
                  {c.title}
                </Select.Option>
              ))}
            </Select>
          </FormControl>
          <span className="spacer" />
          <FormControl>
            <FormControl.Label>Advanced</FormControl.Label>
            <ToggleSwitch
              size="small"
              checked={advanced}
              onClick={() => patch({ adv: !advanced } as never)}
              aria-labelledby="adv-toggle"
            />
            <span id="adv-toggle" hidden>
              Advanced options
            </span>
          </FormControl>
        </div>

        <div className="row wrap" style={{ marginTop: 12 }}>
          <span className="muted fs0">Try:</span>
          {PRESETS.map((p) => (
            <Button key={p.label} size="small" onClick={() => runPreset(p.source, p.sink)}>
              {p.label}
            </Button>
          ))}
        </div>

        {advanced && <AdvancedOptions query={query} onChange={patch} />}

        {ran && (
          <>
            <div className="muted fs0" style={{ marginTop: 12 }}>
              Equivalent CLI command:
            </div>
            <pre className="mono fs0 card" style={{ padding: 10, marginTop: 4, overflowX: "auto" }}>
              {pathsCliEcho(query)}
            </pre>
          </>
        )}
      </div>

      {!ran ? (
        <EmptyState
          icon={<ShieldCheckIcon size={24} />}
          title="Ask a reachability question"
          body={
            <>
              A reachability path is a chain of calls from a <b>source</b> (where untrusted input
              enters) to a <b>sink</b> (a dangerous API). Paths are risk-ranked; sanitizers on the
              way discount the score. Pick a source and a sink above, or start from a preset.
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
              No high-confidence paths were found, so the search widened to speculative edges
              (class-hierarchy dispatch, unresolved calls). <InfoPopover term="mode_widened" /> Treat
              these as leads to verify, not confirmed flows.
            </Flash>
          )}
          {data.truncated && (
            <Flash variant="warning" style={{ marginBottom: 16 }}>
              The search budget was spent before finishing — an empty or short result may mean “ran
              out of budget”, not “safe”.
            </Flash>
          )}
          {data.paths.length === 0 ? (
            <EmptyState
              title="No paths found"
              body={
                <>
                  No call chain connects {sourceInfo?.title.toLowerCase() ?? "the source"} to{" "}
                  {sinkInfo?.title.toLowerCase() ?? "the sink"} in this index. That's good news — but
                  reachability is call-graph based, so dynamic dispatch the indexer couldn't resolve
                  may hide paths (try the advanced toggles).
                </>
              }
            />
          ) : (
            <>
              <div className="muted fs0" style={{ marginBottom: 10 }}>
                {data.paths.length} path{data.paths.length === 1 ? "" : "s"} · risk-ranked, highest
                first · mode: {data.mode}
              </div>
              {data.paths.map((p, i) => (
                <PathFlow key={i} path={p} index={i} />
              ))}
            </>
          )}
        </>
      )}
    </>
  );
}
