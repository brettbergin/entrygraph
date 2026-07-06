import { describe, expect, it } from "vitest";
import { cliEcho, validateSource } from "./wizardLogic";

const base = {
  step: 2 as const,
  sourceKind: "url" as const,
  source: "https://github.com/org/repo",
  ref: "",
  fullClone: false,
  includeTests: false,
};

describe("cliEcho", () => {
  it("renders the minimal command", () => {
    expect(cliEcho(base)).toBe("entrygraph index https://github.com/org/repo");
  });

  it("maps every option to its CLI flag", () => {
    expect(
      cliEcho({ ...base, ref: "v2.1", fullClone: true, includeTests: true }),
    ).toBe("entrygraph index https://github.com/org/repo --ref v2.1 --full-clone --include-tests");
  });

  it("omits --full-clone for local paths", () => {
    expect(
      cliEcho({ ...base, sourceKind: "path", source: "/srv/checkout", fullClone: true }),
    ).toBe("entrygraph index /srv/checkout");
  });
});

describe("validateSource", () => {
  it("accepts https and scp-style URLs", () => {
    expect(validateSource("url", "https://github.com/org/repo.git")).toBeNull();
    expect(validateSource("url", "git@github.com:org/repo.git")).toBeNull();
  });

  it("rejects other schemes and junk", () => {
    expect(validateSource("url", "file:///etc")).not.toBeNull();
    expect(validateSource("url", "not a url")).not.toBeNull();
    expect(validateSource("url", "")).not.toBeNull();
  });

  it("requires absolute local paths", () => {
    expect(validateSource("path", "/srv/checkout")).toBeNull();
    expect(validateSource("path", "relative/path")).not.toBeNull();
  });
});
