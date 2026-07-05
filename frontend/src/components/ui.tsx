import { useCallback, useEffect, useState } from "react";
import { AuthError } from "../api";
import type { ScanCounts } from "../types";

export function Badge({ kind }: { kind: string }) {
  return <span className={`badge ${kind}`}>{kind}</span>;
}

export function Counts({ counts }: { counts: ScanCounts }) {
  return (
    <div className="counts">
      <span>
        <b>{counts.new}</b> new
      </span>
      <span>
        <b>{counts.known}</b> known
      </span>
      <span>
        <b>{counts.fixed}</b> fixed
      </span>
      <span>
        <b>{counts.suppressed}</b> suppressed
      </span>
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error">{message}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
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
