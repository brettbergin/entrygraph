"""entrygraph command-line interface — a thin wrapper over CodeGraph.

All logic lives in the library; this module only translates arguments, calls
the API, and renders results. `main(argv)` returns a process exit code so it is
directly testable.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from entrygraph import CodeGraph, __version__
from entrygraph.cli.render import render_table, to_json
from entrygraph.errors import EntrygraphError

DEFAULT_DB_NAME = ".entrygraph.db"


def _discover_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    current = Path.cwd()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_DB_NAME
        if candidate.exists():
            return candidate
    return current / DEFAULT_DB_NAME


def _open(args) -> CodeGraph:
    return CodeGraph.open(_discover_db(getattr(args, "db", None)))


def _emit(args, rows: list, columns: list[str]) -> None:
    if getattr(args, "json", False):
        print(to_json([asdict(r) if hasattr(r, "__dataclass_fields__") else r for r in rows]))
    else:
        print(render_table([_flatten(r) for r in rows], columns))


def _flatten(obj) -> dict:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj


# ---------------- command handlers ----------------

def cmd_index(args) -> int:
    from entrygraph.pipeline.scanner import index_repository

    root = Path(args.path).resolve()
    db = args.db or (root / DEFAULT_DB_NAME)
    if getattr(args, "full", False) or not Path(db).exists():
        graph = CodeGraph.index(root, db=db)
        stats = graph._last_index_stats
        graph.close()
    else:
        with CodeGraph.open(db) as graph:
            stats = index_repository(root, graph._engine, incremental=True,
                                     paranoid=args.paranoid)
    if args.json:
        print(to_json(stats))
    else:
        print(
            f"Indexed {root}\n"
            f"  files: {stats.files_indexed} indexed, {stats.files_skipped} skipped "
            f"of {stats.files_scanned} scanned\n"
            f"  symbols: {stats.symbols}  edges: {stats.edges}  "
            f"entrypoints: {stats.entrypoints}\n"
            f"  db: {db}  ({stats.duration_seconds}s)"
        )
    return 0


def cmd_detect(args) -> int:
    with _open(args) as graph:
        report = graph.detect()
    if args.json:
        print(to_json(report))
        return 0
    print("Languages:")
    print(render_table(
        [{"language": l.name, "files": l.file_count, "percent": f"{l.percent:.1f}%"}
         for l in report.languages],
        ["language", "files", "percent"],
    ))
    print("\nFrameworks:")
    print(render_table(
        [{"framework": f.name, "language": f.language, "confidence": f"{f.confidence:.2f}"}
         for f in report.frameworks],
        ["framework", "language", "confidence"],
    ))
    return 0


def cmd_symbols(args) -> int:
    with _open(args) as graph:
        rows = graph.symbols(kind=args.kind, name=args.name, qname=args.qname,
                             file=args.file, limit=args.limit)
    _emit(args, rows, ["kind", "qname", "file", "start_line"])
    return 0


def cmd_entrypoints(args) -> int:
    with _open(args) as graph:
        rows = graph.entrypoints(kind=args.kind, framework=args.framework, route=args.route)
    if args.json:
        print(to_json(rows))
    else:
        print(render_table(
            [{"kind": r.kind, "framework": r.framework, "route": r.route,
              "method": r.http_method, "handler": r.symbol.qname} for r in rows],
            ["kind", "framework", "method", "route", "handler"],
        ))
    return 0


def cmd_callers(args) -> int:
    with _open(args) as graph:
        rows = graph.callers(args.qname, depth=args.depth)
    _emit(args, rows, ["kind", "qname", "file"])
    return 0


def cmd_callees(args) -> int:
    with _open(args) as graph:
        rows = graph.callees(args.qname, depth=args.depth)
    _emit(args, rows, ["kind", "qname", "file"])
    return 0


def cmd_paths(args) -> int:
    with _open(args) as graph:
        paths = graph.paths(
            source=args.source,
            sink=args.sink,
            sink_category=args.sink_category,
            max_depth=args.max_depth,
            max_paths=args.max_paths,
            min_confidence=args.min_confidence,
        )
    if args.json:
        print(to_json([
            {"length": len(p.symbols), "min_confidence": p.min_confidence,
             "symbols": [s.qname for s in p.symbols],
             "lines": [e.line for e in p.edges]}
            for p in paths
        ]))
    else:
        if not paths:
            print("(no paths found)")
        for i, path in enumerate(paths, 1):
            print(f"[{i}] {path.render()}")
    return 0 if paths else 1


def cmd_stats(args) -> int:
    with _open(args) as graph:
        stats = graph.stats()
    if args.json:
        print(to_json(stats))
    else:
        for key, value in asdict(stats).items():
            print(f"{key:20} {value}")
    return 0


# ---------------- parser ----------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="entrygraph", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"entrygraph {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p):
        p.add_argument("--db", help=f"index database path (default: discover {DEFAULT_DB_NAME})")
        p.add_argument("--json", action="store_true", help="emit JSON")

    p = sub.add_parser("index", help="index or re-index a repository")
    p.add_argument("path")
    p.add_argument("--db", help=f"database path (default: <path>/{DEFAULT_DB_NAME})")
    p.add_argument("--full", action="store_true", help="force full re-index (default: incremental)")
    p.add_argument("--paranoid", action="store_true", help="hash every file (skip mtime fast path)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("detect", help="show detected languages and frameworks")
    add_db(p)
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("symbols", help="list symbols")
    add_db(p)
    p.add_argument("--kind")
    p.add_argument("--name")
    p.add_argument("--qname")
    p.add_argument("--file")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_symbols)

    p = sub.add_parser("entrypoints", help="list entrypoints")
    add_db(p)
    p.add_argument("--kind")
    p.add_argument("--framework")
    p.add_argument("--route")
    p.set_defaults(func=cmd_entrypoints)

    p = sub.add_parser("callers", help="who calls this symbol")
    add_db(p)
    p.add_argument("qname")
    p.add_argument("--depth", type=int, default=1)
    p.set_defaults(func=cmd_callers)

    p = sub.add_parser("callees", help="what this symbol calls")
    add_db(p)
    p.add_argument("qname")
    p.add_argument("--depth", type=int, default=1)
    p.set_defaults(func=cmd_callees)

    p = sub.add_parser("paths", help="source -> sink call paths")
    add_db(p)
    p.add_argument("--source", required=True, help="qname or glob")
    p.add_argument("--sink", help="qname or glob (e.g. py:subprocess.run)")
    p.add_argument("--sink-category", dest="sink_category",
                   help="named sink category (e.g. command_exec, sql)")
    p.add_argument("--max-depth", dest="max_depth", type=int, default=25)
    p.add_argument("--max-paths", dest="max_paths", type=int, default=10)
    p.add_argument("--min-confidence", dest="min_confidence", type=int, default=0)
    p.set_defaults(func=cmd_paths)

    p = sub.add_parser("stats", help="index statistics")
    add_db(p)
    p.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EntrygraphError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
