import { useState } from "react";
import { api } from "../api";
import type { Finding, Policy, Scan, Suppression } from "../types";
import { Badge, Counts, Empty, ErrorBox, fmtTime, shortSha, useAsync } from "./ui";

type Props = { installationId: number; fullName: string; onAuthError: () => void };
type Tab = "scans" | "suppressions" | "policy";

export function RepoDetail({ installationId, fullName, onAuthError }: Props) {
  const [tab, setTab] = useState<Tab>("scans");
  return (
    <div>
      <div className="tabs">
        {(["scans", "suppressions", "policy"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      {tab === "scans" && <ScansTab {...{ installationId, fullName, onAuthError }} />}
      {tab === "suppressions" && <SuppressionsTab {...{ installationId, fullName, onAuthError }} />}
      {tab === "policy" && <PolicyTab {...{ installationId, fullName, onAuthError }} />}
    </div>
  );
}

function ScansTab({ installationId, fullName, onAuthError }: Props) {
  const [selected, setSelected] = useState<Scan | null>(null);
  const { data, error, loading } = useAsync<Scan[]>(
    () => api.scans(installationId, fullName),
    [installationId, fullName],
    onAuthError,
  );
  if (error) return <ErrorBox message={error} />;
  if (loading) return <Empty>Loading scans…</Empty>;
  if (!data || data.length === 0) return <Empty>No scans yet for this repo.</Empty>;

  if (selected) {
    return (
      <FindingsView
        installationId={installationId}
        fullName={fullName}
        scan={selected}
        onBack={() => setSelected(null)}
        onAuthError={onAuthError}
      />
    );
  }

  return (
    <div className="card">
      <table>
        <thead>
          <tr>
            <th>PR</th>
            <th>Head</th>
            <th>Status</th>
            <th>Counts</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {data.map((s) => (
            <tr key={s.id} onClick={() => setSelected(s)} style={{ cursor: "pointer" }}>
              <td>{s.pr_number ? `#${s.pr_number}` : "—"}</td>
              <td className="mono">{shortSha(s.head_sha)}</td>
              <td>
                <Badge kind={s.status} />
              </td>
              <td>
                <Counts counts={s.counts} />
              </td>
              <td>{fmtTime(s.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FindingsView({
  installationId,
  fullName,
  scan,
  onBack,
  onAuthError,
}: Props & { scan: Scan; onBack: () => void }) {
  const [status, setStatus] = useState<string>("");
  const { data, error, loading } = useAsync<Finding[]>(
    () => api.scanFindings(installationId, fullName, scan.id, status || undefined),
    [installationId, fullName, scan.id, status],
    onAuthError,
  );
  return (
    <div>
      <div className="toolbar">
        <button className="btn ghost" onClick={onBack}>
          ← scans
        </button>
        <span className="mono">
          {scan.pr_number ? `PR #${scan.pr_number} · ` : ""}
          {shortSha(scan.head_sha)}
        </span>
        <Badge kind={scan.status} />
        <div className="spacer" />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          <option value="new">new</option>
          <option value="known">known</option>
          <option value="fixed">fixed</option>
          <option value="suppressed">suppressed</option>
        </select>
      </div>
      {error && <ErrorBox message={error} />}
      {loading ? (
        <Empty>Loading findings…</Empty>
      ) : !data || data.length === 0 ? (
        <Empty>No findings for this filter.</Empty>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Sink</th>
                <th>Source</th>
                <th>Risk</th>
                <th>Fingerprint</th>
              </tr>
            </thead>
            <tbody>
              {data.map((f) => (
                <tr key={f.fingerprint}>
                  <td>
                    <Badge kind={f.status} />
                  </td>
                  <td className="mono">{f.sink_id ?? "—"}</td>
                  <td className="mono">{f.source_category ?? "—"}</td>
                  <td>{f.risk.toFixed(2)}</td>
                  <td className="mono">{f.fingerprint.slice(0, 12)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SuppressionsTab({ installationId, fullName, onAuthError }: Props) {
  const { data, error, loading, reload } = useAsync<Suppression[]>(
    () => api.suppressions(installationId, fullName),
    [installationId, fullName],
    onAuthError,
  );
  const [fp, setFp] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!fp.trim()) return;
    setBusy(true);
    setAddErr(null);
    try {
      await api.addSuppression(installationId, fullName, {
        fingerprint: fp.trim(),
        reason: reason.trim() || undefined,
      });
      setFp("");
      setReason("");
      reload();
    } catch (err) {
      setAddErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(fingerprint: string) {
    await api.removeSuppression(installationId, fullName, fingerprint);
    reload();
  }

  return (
    <div>
      <form className="card" style={{ padding: 16, marginBottom: 16 }} onSubmit={add}>
        <div className="row">
          <input
            style={{ flex: 2 }}
            className="mono"
            placeholder="fingerprint to waive"
            value={fp}
            onChange={(e) => setFp(e.target.value)}
          />
          <input
            style={{ flex: 3 }}
            placeholder="reason (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button className="btn primary" disabled={busy || !fp.trim()}>
            Add waiver
          </button>
        </div>
        {addErr && <div style={{ marginTop: 10 }}>{<ErrorBox message={addErr} />}</div>}
      </form>
      {error && <ErrorBox message={error} />}
      {loading ? (
        <Empty>Loading…</Empty>
      ) : !data || data.length === 0 ? (
        <Empty>No suppressions. Add one above to waive a finding.</Empty>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Fingerprint</th>
                <th>Reason</th>
                <th>By</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.map((s) => (
                <tr key={s.fingerprint}>
                  <td className="mono">{s.fingerprint}</td>
                  <td>{s.reason ?? "—"}</td>
                  <td>{s.created_by ?? "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn danger" onClick={() => remove(s.fingerprint)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function PolicyTab({ installationId, fullName, onAuthError }: Props) {
  const { data, error, loading, reload } = useAsync<Policy>(
    () => api.policy(installationId, fullName),
    [installationId, fullName],
    onAuthError,
  );
  const [draft, setDraft] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const p = draft ?? data;

  if (error) return <ErrorBox message={error} />;
  if (loading || !p) return <Empty>Loading policy…</Empty>;

  function set<K extends keyof Policy>(key: K, value: Policy[K]) {
    setDraft({ ...(p as Policy), [key]: value });
    setSaved(false);
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    try {
      await api.setPolicy(installationId, fullName, draft);
      setSaved(true);
      setDraft(null);
      reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: 20, maxWidth: 460 }}>
      <div style={{ marginBottom: 16 }}>
        <label>Risk threshold (gate at/above)</label>
        <input
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={p.risk_threshold}
          onChange={(e) => set("risk_threshold", parseFloat(e.target.value))}
        />
      </div>
      <div style={{ marginBottom: 16 }}>
        <label>Mode</label>
        <select value={p.mode} onChange={(e) => set("mode", e.target.value)}>
          <option value="block">block (fail the check)</option>
          <option value="warn">warn (neutral)</option>
        </select>
      </div>
      <div style={{ marginBottom: 16 }}>
        <label>Minimum confidence</label>
        <select value={p.min_confidence} onChange={(e) => set("min_confidence", e.target.value)}>
          <option value="exact">exact</option>
          <option value="import">import</option>
          <option value="fuzzy">fuzzy</option>
          <option value="unresolved">unresolved</option>
        </select>
      </div>
      <div style={{ marginBottom: 20 }}>
        <label>Gated categories (comma-separated; blank = all)</label>
        <input
          value={(p.gated_categories ?? []).join(", ")}
          onChange={(e) =>
            set(
              "gated_categories",
              e.target.value.trim()
                ? e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                : null,
            )
          }
        />
      </div>
      <div className="row">
        <button className="btn primary" disabled={busy || !draft} onClick={save}>
          Save policy
        </button>
        {saved && <span style={{ color: "var(--green)" }}>Saved.</span>}
      </div>
    </div>
  );
}
