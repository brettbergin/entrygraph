import { Button, TextInput } from "@primer/react";
import { ScreenFullIcon, SearchIcon, ShareAndroidIcon } from "@primer/octicons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api, keys } from "../../../api/queries";
import { EmptyState } from "../../../components/EmptyState";
import { ErrorFlash } from "../../../components/ui";
import { useRepoId } from "../RepoLayout";
import { CallGraphCanvas, type CanvasHandle } from "./CallGraphCanvas";
import { emptyModel, mergeNeighborhood, refocus, type GraphModel } from "./graphLayout";
import { SymbolDetailPane } from "../SymbolDetailPane";

const NODE_WARN = 300;
const NODE_CAP = 800;

export function CallGraphTab() {
  const repoId = useRepoId();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") ?? "";
  const [draft, setDraft] = useState(focus);
  const [model, setModel] = useState<GraphModel>(() => emptyModel(focus));
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canvas = useRef<CanvasHandle | null>(null);

  const fetchHood = useCallback(
    (qname: string) =>
      queryClient.fetchQuery({
        queryKey: keys.neighborhood(repoId, qname),
        queryFn: () => api.neighborhood(repoId, qname),
      }),
    [queryClient, repoId],
  );

  // (re)seed the model whenever the focused symbol changes
  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    setError(null);
    fetchHood(focus)
      .then((hood) => {
        if (!cancelled) setModel(mergeNeighborhood(emptyModel(focus), hood));
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [focus, fetchHood]);

  const expand = useCallback(
    (qname: string) => {
      setModel((m) => {
        if (m.expanded.has(qname) || m.nodes.size >= NODE_CAP) return m;
        void fetchHood(qname)
          .then((hood) => setModel((cur) => mergeNeighborhood(cur, hood)))
          .catch((err) => setError(String(err)));
        return m;
      });
    },
    [fetchHood],
  );

  const doRefocus = useCallback(
    (qname: string) => {
      setModel((m) => refocus(m, qname));
      setParams(
        (p) => {
          p.set("focus", qname);
          return p;
        },
        { replace: true },
      );
      setDraft(qname);
    },
    [setParams],
  );

  const submitSearch = () => {
    if (draft) {
      setParams(
        (p) => {
          p.set("focus", draft);
          return p;
        },
        { replace: true },
      );
    }
  };

  return (
    <div className={selected ? "split" : undefined}>
      <div>
        <form
          className="row"
          style={{ marginBottom: 12 }}
          onSubmit={(e) => {
            e.preventDefault();
            submitSearch();
          }}
        >
          <TextInput
            leadingVisual={SearchIcon}
            placeholder="Focus a symbol by qualified name (e.g. app.views.create_report)…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            style={{ flex: 1 }}
            className="mono"
          />
          <Button type="submit">Focus</Button>
        </form>

        {error && <ErrorFlash message={error} />}
        {model.nodes.size >= NODE_WARN && (
          <div className="muted fs0" style={{ marginBottom: 8 }}>
            {model.nodes.size} nodes — expand selectively; merges stop at {NODE_CAP}.
          </div>
        )}

        {!focus ? (
          <EmptyState
            icon={<ShareAndroidIcon size={24} />}
            title="Pick a symbol to explore its call graph"
            body={
              <>
                The call graph shows who calls a function (left) and what it calls (right).
                Click a node to inspect it, click a dashed node to expand its neighborhood,
                double-click to refocus. Find a symbol under the Symbols tab and use
                “View in call graph”.
              </>
            }
          />
        ) : (
          <div className="card graph-wrap">
            <CallGraphCanvas
              model={model}
              onSelect={setSelected}
              onExpand={expand}
              onRefocus={doRefocus}
              handleRef={(h) => (canvas.current = h)}
            />
            <div className="graph-toolbar">
              <Button size="small" leadingVisual={ScreenFullIcon} onClick={() => canvas.current?.zoomToFit()}>
                Fit
              </Button>
            </div>
            <div className="card graph-legend">
              <span>
                <span className="swatch" style={{ borderTop: "3px solid var(--accent)" }} /> focused
                symbol
              </span>
              <span>
                <span className="swatch" style={{ borderTop: "2px dashed var(--fg-muted)" }} />{" "}
                unexpanded — click to load its calls
              </span>
              <span className="muted">drag to pan · scroll to zoom · double-click refocuses</span>
            </div>
          </div>
        )}
      </div>
      {selected && <SymbolDetailPane qname={selected} />}
    </div>
  );
}
