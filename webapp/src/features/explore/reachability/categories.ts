// Friendly names + plain-English descriptions for taint categories — the
// teaching layer over the raw catalog ids the API takes.

export interface CategoryInfo {
  id: string;
  title: string;
  description: string;
}

export const SOURCE_CATEGORIES: CategoryInfo[] = [
  {
    id: "http_input",
    title: "HTTP request input",
    description: "Query params, form bodies, headers, cookies — anything a web caller controls.",
  },
  {
    id: "cli_arg",
    title: "Command-line arguments",
    description: "argv and parsed CLI flags — input from whoever invokes the program.",
  },
  {
    id: "env_input",
    title: "Environment variables",
    description: "Values read from the process environment.",
  },
  {
    id: "file_input",
    title: "File contents",
    description: "Data read from files or uploads.",
  },
  {
    id: "all",
    title: "Any tagged source",
    description: "Every place untrusted input enters, across all categories.",
  },
];

export const SINK_CATEGORIES: CategoryInfo[] = [
  {
    id: "command_exec",
    title: "Command execution",
    description: "subprocess, shells, os.system — attacker input here means RCE.",
  },
  {
    id: "sql",
    title: "SQL queries",
    description: "Raw query execution — attacker input here means SQL injection.",
  },
  {
    id: "path_traversal",
    title: "File paths",
    description: "File opens/reads with a caller-influenced path.",
  },
  {
    id: "deserialization",
    title: "Deserialization",
    description: "pickle, yaml.load and friends — code execution via crafted payloads.",
  },
  {
    id: "ssrf",
    title: "Outbound requests (SSRF)",
    description: "HTTP clients called with a caller-influenced URL.",
  },
  {
    id: "all",
    title: "Any tagged sink",
    description: "Every dangerous API in the catalog, across all categories.",
  },
];

export const PRESETS: Array<{ label: string; source: string; sink: string }> = [
  { label: "Web input → shell command", source: "http_input", sink: "command_exec" },
  { label: "Web input → SQL", source: "http_input", sink: "sql" },
  { label: "CLI args → shell command", source: "cli_arg", sink: "command_exec" },
  { label: "Anything → anything", source: "all", sink: "all" },
];
