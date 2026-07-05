import { useState } from "react";
import { Checkbox, Label, Select } from "@primer/react";
import { api } from "../api";
import type { Path } from "../types";
import { Empty, ErrorFlash, Loading, useAsync } from "./ui";

const SOURCES = ["http_input", "cli_arg", "env_input", "user_input", "stdin_input", "all"];
const SINKS = [
  "all", "command_exec", "sql", "code_eval", "deserialization", "path_traversal",
  "ssrf", "template_injection", "file_write", "network_out", "xxe", "dynamic_load",
];

export function Reachability({ repoId }: { repoId: number }) {
  const [source, setSource] = useState("http_input");
  const [sink, setSink] = useState("all");
  const [unresolved, setUnresolved] = useState(false);

  const { data, error, loading } = useAsync(
    () =>
      api.paths(repoId, {
        source_category: source,
        sink_category: sink,
        include_unresolved: unresolved,
      }),
    [repoId, source, sink, unresolved],
  );

  return (
    <div>
      <div className="row wrap section">
        <span className="muted fs0">source</span>
        <Select value={source} onChange={(e) => setSource(e.target.value)}>
          {SOURCES.map((s) => (
            <Select.Option key={s} value={s}>
              {s}
            </Select.Option>
          ))}
        </Select>
        <span className="arrow">→</span>
        <span className="muted fs0">sink</span>
        <Select value={sink} onChange={(e) => setSink(e.target.value)}>
          {SINKS.map((s) => (
            <Select.Option key={s} value={s}>
              {s}
            </Select.Option>
          ))}
        </Select>
        <label className="row" style={{ marginLeft: 8, gap: 6 }}>
          <Checkbox checked={unresolved} onChange={(e) => setUnresolved(e.target.checked)} />
          <span className="muted fs0">include unresolved (noisier)</span>
        </label>
      </div>

      {error ? (
        <ErrorFlash message={error} />
      ) : loading ? (
        <Loading label="Enumerating paths…" />
      ) : !data || data.paths.length === 0 ? (
        <Empty>
          No {source} → {sink} paths.
          {!unresolved && " Try enabling unresolved, or a different sink."}
        </Empty>
      ) : (
        <div className="section">
          <div className="muted fs0" style={{ marginBottom: 10 }}>
            {data.paths.length} path(s){data.mode ? ` · ${data.mode} frontier` : ""}
          </div>
          {data.paths.map((p, i) => (
            <PathRow key={i} path={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function PathRow({ path }: { path: Path }) {
  return (
    <div className="card" style={{ padding: 12, marginBottom: 10 }}>
      <div className="row wrap" style={{ marginBottom: 8 }}>
        {path.risk != null && (
          <Label variant={path.risk >= 0.5 ? "danger" : "attention"}>risk {path.risk.toFixed(2)}</Label>
        )}
        {path.verified === true && <Label variant="success">verified</Label>}
        {path.verified === false && <Label variant="secondary">refuted</Label>}
        {path.source_channel && (
          <span className="muted fs0">
            {path.source_channel}
            {path.source_key ? `:${path.source_key}` : ""}
          </span>
        )}
        {path.may_continue && <span className="muted fs0">…may continue</span>}
      </div>
      <div className="chain">
        {path.hops.map((h, i) => {
          const isSink = i === path.hops.length - 1;
          const isSource = i === 0;
          return (
            <span key={i} className="row" style={{ gap: 6 }}>
              {i > 0 && <span className="arrow">→</span>}
              <span
                className={`hop ${isSink ? "sink" : isSource ? "source" : ""}`}
                title={h.qname}
              >
                {h.qname.startsWith("py:") ||
                h.qname.startsWith("js:") ||
                h.qname.includes(":")
                  ? h.qname
                  : h.name}
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
