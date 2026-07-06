// Pure wizard logic — kept free of component/CSS imports so it's unit-testable.

export type SourceKind = "url" | "path";

export interface WizardState {
  step: 0 | 1 | 2;
  sourceKind: SourceKind;
  source: string;
  ref: string;
  fullClone: boolean;
  includeTests: boolean;
}

const GIT_URL_RE = /^(https:\/\/[^\s]+|[\w.-]+@[\w.-]+:[^\s]+|ssh:\/\/[^\s]+)$/;

export function validateSource(kind: SourceKind, source: string): string | null {
  if (!source.trim()) return "enter a source";
  if (kind === "url" && !GIT_URL_RE.test(source.trim())) {
    return "expected an https:// or ssh (git@host:org/repo.git) git URL";
  }
  if (kind === "path" && !source.trim().startsWith("/")) {
    return "expected an absolute path on the server";
  }
  return null;
}

/** The CLI command equivalent to the wizard's current state — the UI teaches the CLI. */
export function cliEcho(s: WizardState): string {
  const parts = ["entrygraph index", s.source.trim() || "<source>"];
  if (s.ref.trim()) parts.push(`--ref ${s.ref.trim()}`);
  if (s.fullClone && s.sourceKind === "url") parts.push("--full-clone");
  if (s.includeTests) parts.push("--include-tests");
  return parts.join(" ");
}
