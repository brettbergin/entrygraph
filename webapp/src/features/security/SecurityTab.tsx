// Per-repo Security: run the gate, browse the latest scan's findings (with
// suppress), manage the baseline, edit the policy, list suppressions. This is
// the CLI `gate`/`baseline` surface, made interactive.

import {
  Button,
  Flash,
  FormControl,
  Label,
  Select,
  TextInput,
  UnderlineNav,
} from "@primer/react";
import { ShieldCheckIcon } from "@primer/octicons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api, keys } from "../../api/queries";
import { useAuth } from "../../auth/AuthProvider";
import { EmptyState } from "../../components/EmptyState";
import { InfoPopover } from "../../components/InfoPopover";
import { ErrorFlash, Loading, RiskBadge } from "../../components/ui";
import { useRepoId } from "../explore/RepoLayout";
import type { GateResult } from "../../api/types";

const SECTIONS = ["findings", "baseline", "policy", "suppressions"] as const;
type Section = (typeof SECTIONS)[number];

function useIsAdmin() {
  const { me } = useAuth();
  return me == null || me.auth_disabled || me.user.role === "admin";
}

export function SecurityTab() {
  const [params, setParams] = useSearchParams();
  const section = (params.get("s") as Section) ?? "findings";
  const setSection = (s: Section) =>
    setParams(
      (p) => {
        p.set("s", s);
        return p;
      },
      { replace: true },
    );

  return (
    <>
      <UnderlineNav aria-label="Security sections">
        {SECTIONS.map((s) => (
          <UnderlineNav.Item
            key={s}
            aria-current={section === s ? "page" : undefined}
            onSelect={(e) => {
              e.preventDefault();
              setSection(s);
            }}
          >
            {s[0].toUpperCase() + s.slice(1)}
          </UnderlineNav.Item>
        ))}
      </UnderlineNav>
      <div className="section">
        {section === "findings" && <FindingsSection />}
        {section === "baseline" && <BaselineSection />}
        {section === "policy" && <PolicySection />}
        {section === "suppressions" && <SuppressionsSection />}
      </div>
    </>
  );
}

// ---------------- findings + run gate ----------------

function FindingsSection() {
  const repoId = useRepoId();
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const [result, setResult] = useState<GateResult | null>(null);

  const scans = useQuery({ queryKey: keys.scans(repoId), queryFn: () => api.scans(repoId) });
  const run = useMutation({
    mutationFn: () => api.runGate(repoId, {}),
    onSuccess: (r) => {
      setResult(r);
      void queryClient.invalidateQueries({ queryKey: keys.scans(repoId) });
    },
  });
  const suppress = useMutation({
    mutationFn: (fp: string) =>
      api.addSuppression(repoId, { fingerprint: fp, reason: "suppressed from UI" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.suppressions(repoId) }),
  });

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <span>
          Run the <b>gate</b> <InfoPopover term="gate" /> to classify reachable dangerous paths
          against the baseline.
        </span>
        <span className="spacer" />
        {isAdmin && (
          <Button variant="primary" leadingVisual={ShieldCheckIcon} loading={run.isPending} onClick={() => run.mutate()}>
            Run gate
          </Button>
        )}
      </div>
      {run.error && <ErrorFlash message={String(run.error)} />}

      {result && (
        <Flash
          variant={
            result.status === "passed"
              ? "success"
              : result.status === "no-baseline"
                ? "default"
                : "warning"
          }
          style={{ marginBottom: 16 }}
        >
          Gate <b>{result.status}</b> — {result.counts.new} new, {result.counts.known} known,{" "}
          {result.counts.fixed} fixed, {result.counts.suppressed} suppressed.
          {result.status === "no-baseline" && " Cut a baseline to start gating on new paths."}
        </Flash>
      )}

      {result && result.new.length > 0 ? (
        <div className="card">
          {result.new.map((f) => (
            <div
              key={f.fingerprint}
              className="row"
              style={{ padding: "10px 14px", borderTop: "1px solid var(--border)" }}
            >
              <RiskBadge risk={f.risk} />
              {f.sink_id && <Label size="small">{f.sink_id}</Label>}
              <span className="mono fs0 clip">
                {f.hops[0]?.qname} → {f.hops[f.hops.length - 1]?.qname}
              </span>
              <span className="spacer" />
              {isAdmin && (
                <Button
                  size="small"
                  disabled={suppress.isPending}
                  onClick={() => suppress.mutate(f.fingerprint)}
                >
                  Suppress
                </Button>
              )}
            </div>
          ))}
        </div>
      ) : (
        !result && (
          <>
            {scans.isPending ? (
              <Loading />
            ) : scans.data && scans.data.length > 0 ? (
              <div className="card">
                {scans.data.map((s, i) => (
                  <div
                    key={s.id}
                    className="row"
                    style={{ padding: "10px 14px", borderTop: i ? "1px solid var(--border)" : undefined }}
                  >
                    <Label size="small" variant={s.status === "passed" ? "success" : "attention"}>
                      {s.status}
                    </Label>
                    <span className="muted fs0">
                      {s.counts.new} new · {s.counts.known} known · {s.counts.fixed} fixed
                    </span>
                    <span className="spacer" />
                    <span className="muted fs0">{s.created_at?.slice(0, 19).replace("T", " ")}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                title="No gate runs yet"
                body={
                  <>
                    Findings appear when a gate run detects a reachability path not in your
                    baseline. Run the gate above to produce the first scan.
                  </>
                }
              />
            )}
          </>
        )
      )}
    </>
  );
}

// ---------------- baseline ----------------

function BaselineSection() {
  const repoId = useRepoId();
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const branch = "main";
  const baseline = useQuery({
    queryKey: keys.baseline(repoId, branch),
    queryFn: () => api.baseline(repoId, branch),
  });
  const cut = useMutation({
    mutationFn: () => api.cutBaseline(repoId, { branch }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.baseline(repoId, branch) }),
  });

  if (baseline.isPending) return <Loading />;
  if (baseline.error) return <ErrorFlash message={String(baseline.error)} />;

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <span>
          The <b>baseline</b> <InfoPopover term="baseline" /> is the accepted set of paths on{" "}
          <code className="mono">{branch}</code>. The gate fails only on paths introduced after it.
        </span>
        <span className="spacer" />
        {isAdmin && (
          <Button loading={cut.isPending} onClick={() => cut.mutate()}>
            {baseline.data ? "Re-cut baseline" : "Cut baseline"}
          </Button>
        )}
      </div>
      {cut.error && <ErrorFlash message={String(cut.error)} />}
      {!baseline.data ? (
        <EmptyState
          title="No baseline on this branch"
          body="Cut a baseline to accept the current dangerous paths as known, then gate future changes against it."
        />
      ) : (
        <div className="card">
          <div className="row" style={{ padding: "10px 14px" }}>
            <span className="muted fs0">
              {baseline.data.paths.length} accepted paths
              {baseline.data.commit_sha && ` · ${baseline.data.commit_sha.slice(0, 8)}`}
              {baseline.data.created_at && ` · ${baseline.data.created_at.slice(0, 10)}`}
            </span>
          </div>
          {baseline.data.paths.slice(0, 100).map((f) => (
            <div
              key={f.fingerprint}
              className="row"
              style={{ padding: "8px 14px", borderTop: "1px solid var(--border)" }}
            >
              <RiskBadge risk={f.risk} />
              {f.sink_id && <Label size="small">{f.sink_id}</Label>}
              <span className="mono fs0 clip">
                {f.hops[0]?.qname} → {f.hops[f.hops.length - 1]?.qname}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ---------------- policy ----------------

function PolicySection() {
  const repoId = useRepoId();
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const policy = useQuery({ queryKey: keys.policy(repoId), queryFn: () => api.policy(repoId) });
  const save = useMutation({
    mutationFn: (body: Partial<import("../../api/types").PolicyData>) => api.putPolicy(repoId, body),
    onSuccess: (p) => queryClient.setQueryData(keys.policy(repoId), p),
  });

  if (policy.isPending) return <Loading />;
  if (policy.error) return <ErrorFlash message={String(policy.error)} />;
  const p = policy.data;

  return (
    <div className="card" style={{ padding: 20, maxWidth: 520 }}>
      <div className="row">
        <b>Gate policy</b>
        <InfoPopover term="policy" />
      </div>
      <FormControl sx-none="" disabled={!isAdmin}>
        <FormControl.Label>
          Risk threshold <InfoPopover term="risk" />
        </FormControl.Label>
        <FormControl.Caption>New paths at or above this risk gate the build.</FormControl.Caption>
        <TextInput
          type="number"
          min={0}
          max={1}
          step={0.05}
          defaultValue={p.risk_threshold}
          disabled={!isAdmin}
          onBlur={(e) => {
            const v = Number(e.target.value);
            if (v !== p.risk_threshold) save.mutate({ risk_threshold: v });
          }}
        />
      </FormControl>
      <FormControl disabled={!isAdmin}>
        <FormControl.Label>Mode</FormControl.Label>
        <FormControl.Caption>
          block fails CI on new gated paths; warn reports without failing.
        </FormControl.Caption>
        <Select
          value={p.mode}
          disabled={!isAdmin}
          onChange={(e) => save.mutate({ mode: e.target.value as "block" | "warn" })}
        >
          <Select.Option value="block">block</Select.Option>
          <Select.Option value="warn">warn</Select.Option>
        </Select>
      </FormControl>
      <FormControl disabled={!isAdmin}>
        <FormControl.Label>
          Minimum confidence <InfoPopover term="confidence" />
        </FormControl.Label>
        <FormControl.Caption>Never gate below this edge-resolution tier.</FormControl.Caption>
        <Select
          value={p.min_confidence}
          disabled={!isAdmin}
          onChange={(e) =>
            save.mutate({
              min_confidence: e.target.value as import("../../api/types").PolicyData["min_confidence"],
            })
          }
        >
          {["exact", "import", "fuzzy", "unresolved"].map((c) => (
            <Select.Option key={c} value={c}>
              {c}
            </Select.Option>
          ))}
        </Select>
      </FormControl>
      {save.isPending && <span className="muted fs0">saving…</span>}
      {save.error && <ErrorFlash message={String(save.error)} />}
    </div>
  );
}

// ---------------- suppressions ----------------

function SuppressionsSection() {
  const repoId = useRepoId();
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const sup = useQuery({
    queryKey: keys.suppressions(repoId),
    queryFn: () => api.suppressions(repoId),
  });
  const remove = useMutation({
    mutationFn: (fp: string) => api.removeSuppression(repoId, fp),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.suppressions(repoId) }),
  });

  if (sup.isPending) return <Loading />;
  if (sup.error) return <ErrorFlash message={String(sup.error)} />;

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <span>
          <b>Suppressions</b> <InfoPopover term="suppression" /> are reviewed waivers — the gate
          reports them but never fails.
        </span>
      </div>
      {sup.data.length === 0 ? (
        <EmptyState
          title="No suppressions"
          body="Suppress a finding from the Findings tab to waive an accepted risk without failing the gate."
        />
      ) : (
        <div className="card">
          {sup.data.map((s, i) => (
            <div
              key={s.fingerprint}
              className="row"
              style={{ padding: "10px 14px", borderTop: i ? "1px solid var(--border)" : undefined }}
            >
              <span className="mono fs0 clip">{s.fingerprint}</span>
              {s.reason && <span className="muted fs0 clip">{s.reason}</span>}
              <span className="spacer" />
              {s.created_by && <span className="muted fs0">{s.created_by}</span>}
              {isAdmin && (
                <Button
                  size="small"
                  variant="danger"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(s.fingerprint)}
                >
                  Remove
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
