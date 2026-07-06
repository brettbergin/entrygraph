// A small ⓘ that explains a domain term inline — the education layer.

import { InfoIcon } from "@primer/octicons-react";
import { useState } from "react";
import { GLOSSARY } from "../lib/glossary";

export function InfoPopover({ term }: { term: keyof typeof GLOSSARY | string }) {
  const [open, setOpen] = useState(false);
  const entry = GLOSSARY[term];
  if (!entry) return null;
  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-label={`What is ${entry.title}?`}
        className="rowbtn muted"
        style={{ display: "inline-flex", alignItems: "center" }}
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      >
        <InfoIcon size={13} />
      </button>
      {open && (
        <span
          role="tooltip"
          className="card"
          style={{
            position: "absolute",
            zIndex: 30,
            top: "calc(100% + 6px)",
            left: -8,
            width: 300,
            padding: "10px 12px",
            fontSize: 12,
            lineHeight: 1.45,
            boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
          }}
        >
          <b>{entry.title}.</b> {entry.short}
          {entry.long && (
            <>
              <br />
              <span className="muted">{entry.long}</span>
            </>
          )}
        </span>
      )}
    </span>
  );
}
