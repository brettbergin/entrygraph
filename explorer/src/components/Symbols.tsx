import { useMemo, useState } from "react";
import { Button, Select, TextInput } from "@primer/react";
import { api } from "../api";
import type { Symbol, SymbolDetail } from "../types";
import { Empty, ErrorFlash, KindLabel, Loading, loc, useAsync } from "./ui";

const KINDS = ["", "function", "method", "class", "variable", "constant", "field", "module"];

export function Symbols({
  repoId,
  onOpenGraph,
}: {
  repoId: number;
  onOpenGraph: (qname: string) => void;
}) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const { data, error, loading } = useAsync<Symbol[]>(
    () => api.symbols(repoId, { q: q || undefined, kind: kind || undefined, limit: 300 }),
    [repoId, q, kind],
  );

  return (
    <div>
      <div className="row wrap section">
        <TextInput
          leadingVisual={() => <span className="muted">🔍</span>}
          placeholder="search symbols by name…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <Select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <Select.Option key={k} value={k}>
              {k || "all kinds"}
            </Select.Option>
          ))}
        </Select>
        {data && <span className="muted fs0">{data.length} shown</span>}
      </div>

      {error ? (
        <ErrorFlash message={error} />
      ) : loading ? (
        <Loading label="Loading symbols…" />
      ) : !data || data.length === 0 ? (
        <Empty>No symbols match.</Empty>
      ) : (
        <div className="split section">
          <div className="card" style={{ overflow: "auto", maxHeight: "72vh" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <tbody>
                {data.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => setSelected(s.qname)}
                    style={{
                      cursor: "pointer",
                      borderBottom: "1px solid var(--border)",
                      background: selected === s.qname ? "var(--canvas)" : undefined,
                    }}
                  >
                    <td style={{ padding: "9px 12px" }}>
                      <div className="mono clip" style={{ maxWidth: 460 }}>
                        {s.qname}
                      </div>
                      <div className="muted fs0">{loc(s.file, s.line)}</div>
                    </td>
                    <td style={{ padding: "9px 12px", textAlign: "right" }}>
                      <KindLabel kind={s.kind} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Detail repoId={repoId} qname={selected} onOpenGraph={onOpenGraph} onOpen={setSelected} />
        </div>
      )}
    </div>
  );
}

function Detail({
  repoId,
  qname,
  onOpenGraph,
  onOpen,
}: {
  repoId: number;
  qname: string | null;
  onOpenGraph: (q: string) => void;
  onOpen: (q: string) => void;
}) {
  if (!qname) {
    return (
      <div className="card detail muted">Select a symbol to see its callers and callees.</div>
    );
  }
  return <DetailBody key={qname} repoId={repoId} qname={qname} onOpenGraph={onOpenGraph} onOpen={onOpen} />;
}

function DetailBody({
  repoId,
  qname,
  onOpenGraph,
  onOpen,
}: {
  repoId: number;
  qname: string;
  onOpenGraph: (q: string) => void;
  onOpen: (q: string) => void;
}) {
  const { data, error, loading } = useAsync<SymbolDetail>(
    () => api.symbol(repoId, qname),
    [repoId, qname],
  );
  const title = useMemo(() => qname.split(/[.:]/).pop(), [qname]);

  return (
    <div className="card detail">
      {error ? (
        <ErrorFlash message={error} />
      ) : loading || !data ? (
        <Loading label="…" />
      ) : (
        <div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{title}</strong>
            <KindLabel kind={data.symbol.kind} />
          </div>
          <div className="muted fs0 mono clip" style={{ margin: "4px 0 2px" }}>
            {data.symbol.qname}
          </div>
          <div className="muted fs0">{loc(data.symbol.file, data.symbol.line)}</div>
          {data.symbol.signature && (
            <pre
              className="mono"
              style={{
                background: "var(--canvas)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: 8,
                marginTop: 10,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {data.symbol.signature}
            </pre>
          )}
          <Button
            size="small"
            variant="primary"
            onClick={() => onOpenGraph(qname)}
            style={{ marginTop: 10 }}
          >
            View call graph →
          </Button>

          <NeighborList title={`Callers (${data.callers.length})`} items={data.callers} onOpen={onOpen} />
          <NeighborList title={`Callees (${data.callees.length})`} items={data.callees} onOpen={onOpen} />
        </div>
      )}
    </div>
  );
}

function NeighborList({
  title,
  items,
  onOpen,
}: {
  title: string;
  items: Symbol[];
  onOpen: (q: string) => void;
}) {
  return (
    <div style={{ marginTop: 14 }}>
      <div className="muted fs0" style={{ textTransform: "uppercase", letterSpacing: 0.4 }}>
        {title}
      </div>
      {items.length === 0 ? (
        <div className="muted fs0" style={{ marginTop: 4 }}>
          none
        </div>
      ) : (
        <div style={{ marginTop: 6 }}>
          {items.slice(0, 40).map((s) => (
            <div key={s.id} style={{ padding: "3px 0" }}>
              <button className="rowbtn mono clip" style={{ maxWidth: 330 }} onClick={() => onOpen(s.qname)}>
                {s.qname}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
