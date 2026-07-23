import { FormControl, Label, Select } from "@primer/react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";
import { EntrypointDetailPane } from "./EntrypointDetailPane";
import { useRepoId } from "./RepoLayout";

const KINDS = [
  "",
  "http_route",
  "cli_command",
  "main",
  "task",
  "lambda_handler",
  "event_handler",
  "middleware",
  "rpc_handler",
  "graphql_resolver",
];

const METHOD_COLOR: Record<string, "success" | "accent" | "attention" | "danger" | "secondary"> = {
  GET: "success",
  POST: "accent",
  PUT: "attention",
  PATCH: "attention",
  DELETE: "danger",
};

export function EntrypointsTab() {
  const repoId = useRepoId();
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const selectedId = Number(params.get("sel") ?? "") || null;

  const filters: Record<string, string> = {};
  if (kind) filters.kind = kind;

  const { data, isPending, error } = useQuery({
    queryKey: keys.entrypoints(repoId, filters),
    queryFn: () => api.entrypoints(repoId, filters),
  });

  if (isPending) return <Loading label="Loading entrypoints…" />;
  if (error) return <ErrorFlash message={String(error)} />;

  const selected = selectedId != null ? data.find((e) => e.id === selectedId) : undefined;

  const select = (id: number) =>
    setParams(
      (p) => {
        if (Number(p.get("sel")) === id) p.delete("sel");
        else p.set("sel", String(id));
        return p;
      },
      { replace: true },
    );

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <FormControl>
          <FormControl.Label visuallyHidden>Kind</FormControl.Label>
          <Select
            value={kind}
            onChange={(e) =>
              setParams(
                (p) => {
                  if (e.target.value) p.set("kind", e.target.value);
                  else p.delete("kind");
                  return p;
                },
                { replace: true },
              )
            }
          >
            {KINDS.map((k) => (
              <Select.Option key={k} value={k}>
                {k || "all kinds"}
              </Select.Option>
            ))}
          </Select>
        </FormControl>
        <span className="muted fs0">
          Entrypoints are where outside input enters your code — HTTP routes, CLI commands,
          scheduled tasks, lambda handlers. Select one to see its parameters and data flow.
        </span>
      </div>

      {data.length === 0 ? (
        <EmptyState
          title="No entrypoints detected"
          body="Libraries often have none — entrypoints come from framework detection (Flask routes, Click commands, …). Try Symbols to browse definitions instead."
        />
      ) : (
        <div className={selected ? "split" : undefined}>
          <div className="card">
            {data.map((e, i) => (
              <div
                key={e.id}
                className="row"
                style={{
                  padding: "8px 14px",
                  borderTop: i ? "1px solid var(--border)" : undefined,
                  background: e.id === selectedId ? "var(--bg-subtle, rgba(0,0,0,0.04))" : undefined,
                }}
              >
                {e.http_method ? (
                  <Label size="small" variant={METHOD_COLOR[e.http_method] ?? "secondary"}>
                    {e.http_method}
                  </Label>
                ) : (
                  <Label size="small" variant="secondary">
                    {e.kind}
                  </Label>
                )}
                <button className="rowbtn mono clip" onClick={() => select(e.id)}>
                  {e.route ?? e.handler?.qname ?? "—"}
                </button>
                {e.parameters.length > 0 && (
                  <Label size="small" variant="secondary">
                    {e.parameters.length} param{e.parameters.length > 1 ? "s" : ""}
                  </Label>
                )}
                <span className="muted fs0">{e.framework}</span>
                <span className="spacer" />
                {e.handler && (
                  <Link
                    className="mono fs0 clip"
                    style={{ maxWidth: 380 }}
                    to={`/repos/${repoId}/symbols?sel=${encodeURIComponent(e.handler.qname)}`}
                  >
                    {e.handler.qname}
                  </Link>
                )}
              </div>
            ))}
          </div>
          {selected && <EntrypointDetailPane entrypoint={selected} />}
        </div>
      )}
    </>
  );
}
