import { useCallback, useEffect, useState } from "react";
import { Flash, Label, type LabelProps, Spinner } from "@primer/react";
import { Blankslate } from "@primer/react/experimental";
import { AuthError } from "../api";
import type { ScanCounts } from "../types";

const STATUS_VARIANT: Record<string, LabelProps["variant"]> = {
  passed: "success",
  known: "success",
  fixed: "success",
  failed: "danger",
  new: "danger",
  warned: "attention",
  "no-baseline": "attention",
  suppressed: "secondary",
  neutral: "secondary",
};

export function StatusLabel({ status }: { status: string }) {
  return <Label variant={STATUS_VARIANT[status] ?? "secondary"}>{status}</Label>;
}

export function Counts({ counts }: { counts: ScanCounts }) {
  return (
    <span className="muted fs0">
      <span className="strong">{counts.new}</span> new ·{" "}
      <span className="strong">{counts.known}</span> known ·{" "}
      <span className="strong">{counts.fixed}</span> fixed ·{" "}
      <span className="strong">{counts.suppressed}</span> suppressed
    </span>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="section center" style={{ padding: 40 }}>
      <Spinner size="medium" />
      <div className="muted" style={{ marginTop: 12 }}>
        {label}
      </div>
    </div>
  );
}

export function ErrorFlash({ message }: { message: string }) {
  return (
    <div className="section">
      <Flash variant="danger">{message}</Flash>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="section">
      <Blankslate>
        <Blankslate.Heading>{title}</Blankslate.Heading>
        {children && <Blankslate.Description>{children}</Blankslate.Description>}
      </Blankslate>
    </div>
  );
}

export function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "—";
}

export function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/**
 * Load async data, re-running when `deps` change. On an AuthError it calls
 * `onAuthError` so the app can drop back to the token screen.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[],
  onAuthError: () => void,
): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const run = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((d) => !cancelled && setData(d))
      .catch((e) => {
        if (e instanceof AuthError) return onAuthError();
        if (!cancelled) setError(e.message ?? String(e));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  useEffect(run, [run]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
