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

from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

from entrygraph import CodeGraph, __version__
from entrygraph.cli import render
from entrygraph.cli.render import (
    confidence_text,
    console,
    entrypoint_kind_text,
    kind_text,
    method_text,
    risk_style,
    risk_text,
    to_json,
)
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


def _percent_bar(percent: float, width: int = 12) -> Text:
    filled = round(percent / 100 * width)
    style = "cyan" if percent >= 25 else "blue"
    bar = Text("█" * filled, style=style)
    bar.append("░" * (width - filled), style="dim")
    bar.append(f" {percent:5.1f}%", style="")
    return bar


def _confidence_bar(confidence: float, width: int = 10) -> Text:
    filled = round(confidence * width)
    style = "green" if confidence >= 0.8 else "yellow" if confidence >= 0.5 else "red"
    bar = Text("█" * filled, style=style)
    bar.append("░" * (width - filled), style="dim")
    bar.append(f" {confidence:.2f}", style=style)
    return bar


# ---------------- command handlers ----------------

def cmd_index(args) -> int:
    from entrygraph.pipeline.scanner import index_repository

    root = Path(args.path).resolve()
    db = args.db or (root / DEFAULT_DB_NAME)
    con = console()

    def _run():
        if getattr(args, "full", False) or not Path(db).exists():
            graph = CodeGraph.index(root, db=db)
            stats = graph._last_index_stats
            graph.close()
            return stats
        with CodeGraph.open(db) as graph:
            return index_repository(root, graph._engine, incremental=True,
                                    paranoid=args.paranoid)

    if args.json:
        print(to_json(_run()))
        return 0

    mode = "full re-index" if getattr(args, "full", False) else "index"
    with con.status(f"[bold]Running {mode}[/] on [cyan]{root}[/]…", spinner="dots"):
        stats = _run()

    body = Text()
    body.append("files    ", style="bold")
    body.append(f"{stats.files_indexed} indexed", style="green")
    body.append(f", {stats.files_skipped} skipped, {stats.files_deleted} deleted "
                f"of {stats.files_scanned} scanned\n", style="dim")
    body.append("graph    ", style="bold")
    body.append(f"{stats.symbols} ", style="cyan")
    body.append("symbols  ", style="dim")
    body.append(f"{stats.edges} ", style="cyan")
    body.append("edges  ", style="dim")
    body.append(f"{stats.entrypoints} ", style="cyan")
    body.append("entrypoints\n", style="dim")
    body.append("db       ", style="bold")
    body.append(f"{db}", style="")
    con.print(Panel(body, title=f"[bold green]✓[/] indexed [cyan]{root.name}[/]",
                    subtitle=f"[dim]{stats.duration_seconds}s[/]",
                    border_style="green", expand=False))
    return 0


def cmd_detect(args) -> int:
    with _open(args) as graph:
        report = graph.detect()
    if args.json:
        print(to_json(report))
        return 0
    con = console()

    langs = render.table("Languages")
    langs.add_column("LANGUAGE", style="bold")
    langs.add_column("FILES", justify="right")
    langs.add_column("SHARE")
    for l in report.languages:
        langs.add_row(l.name, str(l.file_count), _percent_bar(l.percent))
    con.print(langs)

    if report.frameworks:
        fw = render.table("Frameworks")
        fw.add_column("FRAMEWORK", style="bold magenta")
        fw.add_column("LANGUAGE", style="dim")
        fw.add_column("CONFIDENCE")
        for f in report.frameworks:
            fw.add_row(f.name, f.language, _confidence_bar(f.confidence))
        con.print(fw)
    else:
        con.print("[dim]No frameworks detected.[/]")
    return 0


def _print_symbol_table(rows, *, with_line: bool) -> None:
    con = console()
    if not rows:
        con.print("[dim](no results)[/]")
        return
    tbl = render.table()
    tbl.add_column("KIND", no_wrap=True)
    tbl.add_column("QNAME", style="bold", no_wrap=True)
    tbl.add_column("FILE", style="dim", overflow="fold")
    if with_line:
        tbl.add_column("LINE", justify="right", style="dim", no_wrap=True)
    for r in rows:
        cells = [kind_text(r.kind), r.qname, render.cell(r.file)]
        if with_line:
            cells.append(str(r.start_line))
        tbl.add_row(*cells)
    con.print(tbl)


def cmd_symbols(args) -> int:
    with _open(args) as graph:
        rows = graph.symbols(kind=args.kind, name=args.name, qname=args.qname,
                             file=args.file, limit=args.limit)
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=True)
    return 0


def cmd_entrypoints(args) -> int:
    with _open(args) as graph:
        rows = graph.entrypoints(kind=args.kind, framework=args.framework, route=args.route)
    if args.json:
        print(to_json(rows))
        return 0
    con = console()
    if not rows:
        con.print("[dim](no entrypoints)[/]")
        return 0
    tbl = render.table(caption=f"[dim]{len(rows)} entrypoint(s)[/]")
    # short columns are protected from squeeze; HANDLER wraps if the terminal is narrow
    tbl.add_column("KIND", no_wrap=True)
    tbl.add_column("FRAMEWORK", style="magenta", no_wrap=True)
    tbl.add_column("METHOD", no_wrap=True)
    tbl.add_column("ROUTE", style="bold", no_wrap=True)
    tbl.add_column("HANDLER", style="dim", overflow="fold")
    for r in rows:
        tbl.add_row(entrypoint_kind_text(r.kind), render.cell(r.framework),
                    method_text(r.http_method), render.cell(r.route), r.symbol.qname)
    con.print(tbl)
    return 0


def cmd_callers(args) -> int:
    with _open(args) as graph:
        rows = graph.callers(args.qname, depth=args.depth)
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=False)
    return 0


def cmd_callees(args) -> int:
    with _open(args) as graph:
        rows = graph.callees(args.qname, depth=args.depth)
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=False)
    return 0


def _path_tree(index: int, path) -> Tree:
    src = path.symbols[0]
    header = Text()
    header.append(f"[{index}] ", style="dim")
    header.append("■ ", style=risk_style(path.risk_score))
    header.append("risk ", style="dim")
    header.append(risk_text(path.risk_score))
    header.append(f"  {src.qname}", style="bold")
    tree = Tree(header, guide_style="dim")
    node = tree
    for edge, sym in zip(path.edges, path.symbols[1:]):
        is_sink = sym is path.symbols[-1]
        label = Text()
        label.append("→ ", style="dim")
        label.append(sym.qname, style="bold red" if is_sink else kind_text(sym.kind).style)
        label.append(f"   line {edge.line}", style="dim")
        label.append("  ")
        label.append_text(confidence_text(edge.confidence))
        if edge.via:
            label.append(f" via {edge.via}", style="dim italic")
        if is_sink and edge.sink_id:
            label.append(f"  ⚑ {edge.sink_id}", style="red")
        if is_sink and edge.constant_args:
            label.append("  [const-args]", style="dim green")
        node = node.add(label)
    if path.may_continue:
        node.add(Text("… may continue (dynamic/excluded edges)", style="dim italic yellow"))
    return tree


def cmd_paths(args) -> int:
    with _open(args) as graph:
        paths = graph.paths(
            source=args.source,
            sink=args.sink,
            sink_category=args.sink_category,
            max_depth=args.max_depth,
            max_paths=args.max_paths,
            min_confidence=args.min_confidence,
            include_fuzzy=args.include_fuzzy,
            include_unresolved=args.include_unresolved,
            prune_sanitized=args.prune_sanitized,
        )
    if args.json:
        print(to_json([
            {"length": len(p.symbols), "min_confidence": p.min_confidence,
             "risk_score": p.risk_score, "may_continue": p.may_continue,
             "symbols": [s.qname for s in p.symbols],
             "lines": [e.line for e in p.edges]}
            for p in paths
        ]))
        return 0 if paths else 1

    con = console()
    if not paths:
        con.print(Panel("[yellow]No source → sink paths found.[/]",
                        border_style="yellow", expand=False))
        return 1
    target = args.sink or (args.sink_category and f"category:{args.sink_category}") or "sink"
    con.print(f"[bold]{len(paths)}[/] path(s)  [dim]{args.source} → {target}[/]\n")
    for i, path in enumerate(paths, 1):
        con.print(_path_tree(i, path))
    return 0


def cmd_stats(args) -> int:
    with _open(args) as graph:
        stats = graph.stats()
    if args.json:
        print(to_json(stats))
        return 0
    con = console()
    grid = render.table()
    grid.add_column("metric", style="dim")
    grid.add_column("value", justify="right", style="bold cyan")
    for key, value in asdict(stats).items():
        grid.add_row(key, str(value))
    con.print(Panel(grid, title="[bold]index stats[/]", border_style="cyan", expand=False))
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
    p.add_argument("--min-confidence", dest="min_confidence", type=int, default=None,
                   help="explicit confidence floor (overrides --include-* flags)")
    p.add_argument("--include-fuzzy", dest="include_fuzzy", action="store_true",
                   help="also traverse speculative class-hierarchy (CHA) edges")
    p.add_argument("--include-unresolved", dest="include_unresolved", action="store_true",
                   help="also traverse unresolved wildcard-sink and dynamic-call edges")
    p.add_argument("--prune-sanitized", dest="prune_sanitized", action="store_true",
                   help="drop paths neutralized by a registered sanitizer")
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
        console(stderr=True).print(Text(f"error: {exc}", style="bold red"))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
