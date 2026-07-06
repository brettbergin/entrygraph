import { describe, expect, it } from "vitest";
import { pathsCliEcho } from "./queryEcho";

describe("pathsCliEcho", () => {
  it("renders the category form", () => {
    expect(pathsCliEcho({ source_category: "http_input", sink_category: "command_exec" })).toBe(
      "entrygraph paths --source-category http_input --sink-category command_exec",
    );
  });

  it("prefers explicit source/sink globs over categories", () => {
    expect(
      pathsCliEcho({ source_category: "http_input", source: "app.*", sink: "py:subprocess.*" }),
    ).toBe("entrygraph paths --source 'app.*' --sink 'py:subprocess.*'");
  });

  it("appends flags and non-default numbers", () => {
    expect(
      pathsCliEcho({
        source_category: "all",
        sink_category: "sql",
        confirmed_only: true,
        include_fuzzy: true,
        min_confidence: "exact",
        max_depth: 30,
        taint_hops: 5,
      }),
    ).toBe(
      "entrygraph paths --source-category all --sink-category sql --min-confidence exact " +
        "--include-fuzzy --confirmed-only --max-depth 30",
    );
  });

  it("omits default max_depth and taint_hops", () => {
    expect(pathsCliEcho({ source_category: "all", sink_category: "all", max_depth: 25, taint_hops: 5 })).toBe(
      "entrygraph paths --source-category all --sink-category all",
    );
  });
});
