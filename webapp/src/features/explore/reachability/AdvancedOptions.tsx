// Full CLI `paths` flag surface, progressively disclosed. Each toggle carries
// humanized help + a glossary popover for the deep version.

import { Checkbox, FormControl, Select, TextInput } from "@primer/react";
import { InfoPopover } from "../../../components/InfoPopover";
import type { PathsQuery } from "../../../api/types";

interface Props {
  query: PathsQuery;
  onChange: (patch: Partial<PathsQuery>) => void;
}

const TOGGLES: Array<{
  key: keyof PathsQuery;
  label: string;
  help: string;
  term?: string;
}> = [
  { key: "strict", label: "Strict (precise only)", help: "Never widen to speculative edges — resolved calls only." },
  { key: "include_fuzzy", label: "Include class-hierarchy edges", help: "Add virtual-dispatch fan-out.", term: "cha" },
  { key: "include_unresolved", label: "Include unresolved calls", help: "Add wildcard-sink guesses (e.g. any .execute)." },
  { key: "include_callbacks", label: "Follow callbacks", help: "Track functions passed as arguments.", term: "callback" },
  { key: "explicit_sources", label: "Explicit sources only", help: "Require a proven input read; drop handler-as-source." },
  { key: "confirmed_only", label: "Taint-verified only", help: "Keep only paths where flow to the sink is confirmed.", term: "taint_verified" },
];

export function AdvancedOptions({ query, onChange }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 12 }}>
      <div className="row wrap" style={{ gap: 16, alignItems: "end" }}>
        <FormControl>
          <FormControl.Label>Source override (qname/glob)</FormControl.Label>
          <TextInput
            className="mono"
            placeholder="app.views.*"
            defaultValue={query.source ?? ""}
            onBlur={(e) => onChange({ source: e.target.value || undefined })}
          />
        </FormControl>
        <FormControl>
          <FormControl.Label>Sink override (qname/glob)</FormControl.Label>
          <TextInput
            className="mono"
            placeholder="py:subprocess.*"
            defaultValue={query.sink ?? ""}
            onBlur={(e) => onChange({ sink: e.target.value || undefined })}
          />
        </FormControl>
        <FormControl>
          <FormControl.Label>
            Min confidence <InfoPopover term="confidence" />
          </FormControl.Label>
          <Select
            value={query.min_confidence ?? ""}
            onChange={(e) =>
              onChange({ min_confidence: (e.target.value || undefined) as PathsQuery["min_confidence"] })
            }
          >
            <Select.Option value="">adaptive</Select.Option>
            {["exact", "import", "fuzzy", "unresolved"].map((c) => (
              <Select.Option key={c} value={c}>
                {c}
              </Select.Option>
            ))}
          </Select>
        </FormControl>
      </div>

      <div className="row wrap" style={{ gap: 16 }}>
        <FormControl>
          <FormControl.Label>Max depth</FormControl.Label>
          <TextInput
            type="number"
            min={1}
            max={50}
            defaultValue={query.max_depth ?? 25}
            onBlur={(e) => onChange({ max_depth: Number(e.target.value) })}
          />
        </FormControl>
        <FormControl>
          <FormControl.Label>Taint hops</FormControl.Label>
          <TextInput
            type="number"
            min={0}
            max={10}
            defaultValue={query.taint_hops ?? 5}
            onBlur={(e) => onChange({ taint_hops: Number(e.target.value) })}
          />
        </FormControl>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {TOGGLES.map((t) => (
          <FormControl key={String(t.key)}>
            <Checkbox
              checked={Boolean(query[t.key])}
              onChange={(e) => onChange({ [t.key]: e.target.checked } as Partial<PathsQuery>)}
            />
            <FormControl.Label>
              {t.label} {t.term && <InfoPopover term={t.term} />}
            </FormControl.Label>
            <FormControl.Caption>{t.help}</FormControl.Caption>
          </FormControl>
        ))}
      </div>
    </div>
  );
}
