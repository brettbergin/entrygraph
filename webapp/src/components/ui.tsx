import { Flash, Spinner, Label } from "@primer/react";

export function Loading({ label }: { label?: string }) {
  return (
    <div className="empty">
      <Spinner size="medium" />
      {label && <div className="muted fs0">{label}</div>}
    </div>
  );
}

export function ErrorFlash({ message }: { message: string }) {
  return <Flash variant="danger">{message}</Flash>;
}

const CONFIDENCE_LABELS: Record<number, { text: string; variant: "success" | "accent" | "attention" | "severe" }> = {
  3: { text: "exact", variant: "success" },
  2: { text: "import", variant: "accent" },
  1: { text: "fuzzy", variant: "attention" },
  0: { text: "unresolved", variant: "severe" },
};

export function ConfidenceBadge({ confidence }: { confidence: number }) {
  const c = CONFIDENCE_LABELS[confidence] ?? CONFIDENCE_LABELS[0];
  return (
    <Label size="small" variant={c.variant} title={`edge resolution confidence: ${c.text}`}>
      {c.text}
    </Label>
  );
}
