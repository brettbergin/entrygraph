import { FormControl, Select, TextInput } from "@primer/react";
import { SearchIcon } from "@primer/octicons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { api, keys } from "../../api/queries";
import { EmptyState } from "../../components/EmptyState";
import { ErrorFlash, Loading } from "../../components/ui";
import { useRepoId } from "./RepoLayout";
import { SymbolDetailPane } from "./SymbolDetailPane";

const KINDS = ["", "function", "method", "class", "module", "variable", "constant", "interface"];

export function SymbolsTab() {
  const repoId = useRepoId();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = params.get("kind") ?? "";
  const selected = params.get("sel") ?? "";
  const [draft, setDraft] = useState(q);

  // debounce the search box into the URL (the URL is the query state)
  useEffect(() => {
    const t = setTimeout(() => {
      if (draft !== q) {
        setParams(
          (p) => {
            if (draft) p.set("q", draft);
            else p.delete("q");
            return p;
          },
          { replace: true },
        );
      }
    }, 250);
    return () => clearTimeout(t);
  }, [draft, q, setParams]);

  const filters: Record<string, string> = { limit: "200" };
  if (q) filters.q = q;
  if (kind) filters.kind = kind;

  const { data, isPending, error } = useQuery({
    queryKey: keys.symbols(repoId, filters),
    queryFn: () => api.symbols(repoId, filters),
  });

  const select = (qname: string) =>
    setParams(
      (p) => {
        p.set("sel", qname);
        return p;
      },
      { replace: true },
    );

  return (
    <div className={selected ? "split" : undefined}>
      <div>
        <div className="row" style={{ marginBottom: 12 }}>
          <TextInput
            leadingVisual={SearchIcon}
            placeholder="Search symbols by name…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            style={{ flex: 1 }}
          />
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
                  {k || "any kind"}
                </Select.Option>
              ))}
            </Select>
          </FormControl>
        </div>

        {isPending ? (
          <Loading label="Searching…" />
        ) : error ? (
          <ErrorFlash message={String(error)} />
        ) : data.length === 0 ? (
          <EmptyState
            title="No symbols match"
            body="A symbol is any named definition — function, method, class, module. Try a shorter search or clear the kind filter."
          />
        ) : (
          <div className="card">
            {data.map((s, i) => (
              <div
                key={s.id}
                className="row"
                style={{
                  padding: "8px 14px",
                  borderTop: i ? "1px solid var(--border)" : undefined,
                }}
              >
                <span className="muted fs0" style={{ width: 70 }}>
                  {s.kind}
                </span>
                <button className="rowbtn mono clip" onClick={() => select(s.qname)}>
                  {s.qname}
                </button>
                <span className="spacer" />
                <span className="muted fs0 mono clip" style={{ maxWidth: 260 }}>
                  {s.file}
                  {s.line ? `:${s.line}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      {selected && <SymbolDetailPane qname={selected} />}
    </div>
  );
}
