import { useCallback, useEffect, useState } from "react";
import { Flash, Label, type LabelProps, Spinner } from "@primer/react";

const KIND_VARIANT: Record<string, LabelProps["variant"]> = {
  function: "accent",
  method: "done",
  class: "success",
  variable: "secondary",
  constant: "secondary",
  field: "secondary",
  property: "secondary",
  module: "primary",
  http_route: "accent",
  cli_command: "attention",
  main: "attention",
  task: "done",
  lambda_handler: "done",
  middleware: "secondary",
};

export function KindLabel({ kind }: { kind: string }) {
  return <Label variant={KIND_VARIANT[kind] ?? "secondary"}>{kind}</Label>;
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

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function loc(file: string | null, line?: number): string {
  if (!file) return "—";
  return line ? `${file}:${line}` : file;
}

export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[],
): { data: T | null; error: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message ?? String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(run, [run]);
  return { data, error, loading };
}
