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

from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from entrygraph import CodeGraph, __version__
from entrygraph.cli import render
from entrygraph.cli.render import (
    confidence_text,
    console,
    entrypoint_kind_text,
    kind_text,
    method_text,
    to_json,
)
from entrygraph.errors import EntrygraphError
from entrygraph.kinds import Confidence

DEFAULT_DB_NAME = ".entrygraph.db"


def global_db_path() -> Path:
    """The shared, home-dir index that holds every repository's graph (#116).

    Keeping the database out of each project's root means one `index` populates a
    single global store; queries select the relevant repo by working directory."""
    directory = Path.home() / ".entrygraph"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / DEFAULT_DB_NAME


def _discover_db(explicit: str | None) -> Path:
    return Path(explicit) if explicit else global_db_path()


def _indexed_roots(db_path: Path) -> list[str]:
    """Root paths of every repository indexed in ``db_path`` (empty if the DB is
    absent). One small read, reused by cwd-scoping and --repo resolution."""
    if not db_path.exists():
        return []
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from entrygraph.db import models
    from entrygraph.db.engine import make_engine

    engine = make_engine(db_path)
    try:
        with Session(engine) as session:
            return [r for (r,) in session.execute(select(models.Repository.root_path))]
    finally:
        engine.dispose()


def _current_repo_root(db_path: Path) -> str | None:
    """The indexed repository whose root is the working directory or its nearest
    ancestor, so `entrygraph paths` run inside a repo scopes to that repo in the
    global DB. None when the cwd isn't under any indexed repo."""
    here = {Path.cwd(), *Path.cwd().parents}
    matches = [r for r in _indexed_roots(db_path) if Path(r) in here]
    return max(matches, key=len) if matches else None  # nearest ancestor wins


def _resolve_repo(db_path: Path, repo: str | None) -> str | None:
    """Which repository a query binds to. With no ``--repo``, fall back to the repo
    containing the working directory. With ``--repo``, match it against the indexed
    roots by exact path or by trailing name (`--repo acme-api`), erroring clearly on
    an unknown or ambiguous value rather than silently querying the wrong repo."""
    if repo is None:
        return _current_repo_root(db_path)
    roots = _indexed_roots(db_path)
    if not roots:
        return None  # empty/absent DB — let CodeGraph.open raise the precise error
    target = str(Path(repo).expanduser().resolve())
    if target in roots:
        return target
    named = [r for r in roots if Path(r).name == repo]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        raise EntrygraphError(
            f"--repo {repo!r} is ambiguous; matches: {', '.join(sorted(named))}. "
            "Use the full root path (see `entrygraph repos`)."
        )
    raise EntrygraphError(
        f"no indexed repository matching --repo {repo!r}; run `entrygraph repos` to list them"
    )


def _open(args) -> CodeGraph:
    db = _discover_db(getattr(args, "db", None))
    # --repo wins; otherwise bind to the repo the cwd is in (or the sole repo of a
    # single-repo DB).
    root = _resolve_repo(db, getattr(args, "repo", None))
    return CodeGraph.open(db, root=root)


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


def _catalog_coverage():
    """Language-name -> CatalogCoverage for the built-in registry (#95)."""
    from entrygraph.detect.taint import builtin_registry, catalog_coverage

    return catalog_coverage(builtin_registry())


def _coverage_cell(cov) -> Text:
    if cov is None:
        return Text("none", style="red")
    style = {"full": "green", "partial": "yellow", "minimal": "red"}[cov.tier]
    cell = Text(cov.tier, style=style)
    cell.append(f"  {cov.sinks} sinks · {cov.sources} sources", style="dim")
    return cell


def _thin_coverage_note(languages, path_count: int) -> str | None:
    """One-line caveat when a low `paths` result may reflect catalog coverage,
    not codebase safety (#95). `languages` are DetectedLanguage rows (dominant
    first); fires for the dominant language when its tier isn't `full`."""
    if path_count >= 3 or not languages:
        return None
    dominant = max(languages, key=lambda lang: lang.percent)
    cov = _catalog_coverage().get(dominant.name)
    if cov is None:
        return (
            f"note: {dominant.name} has no taint catalog — a low result reflects "
            "missing coverage, not safety."
        )
    if cov.tier == "full":
        return None
    return (
        f"note: {dominant.name} has {cov.tier} taint coverage ({cov.sinks} sinks, "
        f"{cov.sources} sources) — a low result may reflect coverage, not safety."
    )


# ---------------- command handlers ----------------


def cmd_index(args) -> int:
    from contextlib import ExitStack

    from entrygraph.fs.remote import is_git_url, prepare_source
    from entrygraph.pipeline.scanner import index_repository

    con = console()
    is_url = is_git_url(args.path)
    depth = 0 if getattr(args, "full_clone", False) else args.depth

    stack = ExitStack()
    prepare = prepare_source(
        args.path,
        ref=args.ref,
        depth=depth,
        clone_dir=args.clone_dir,
        ephemeral=args.ephemeral,
        timeout=args.timeout,
    )
    if is_url and not args.json:
        with con.status(f"[bold]Cloning[/] [cyan]{args.path}[/]…", spinner="dots"):
            src = stack.enter_context(prepare)
    else:
        src = stack.enter_context(prepare)

    with stack:
        root = src.root
        # Everything indexes into the shared global DB by default; each repo is
        # keyed by its root_path there, so no per-repo db file is needed (#116).
        # --db still overrides (e.g. an isolated throwaway database in CI).
        db = Path(args.db) if args.db else global_db_path()

        def _run():
            if getattr(args, "full", False) or not Path(db).exists():
                graph = CodeGraph.index(root, db=db, include_tests=args.include_tests)
                stats = graph._last_index_stats
                graph.close()
                return stats
            # Index against the engine directly, not a repo-bound CodeGraph.
            # index_repository upserts the repo row itself, so it needs no
            # binding — and CodeGraph.open(db) in a multi-repo global DB is
            # ambiguous and raises "database holds multiple repositories",
            # which broke the default incremental path (#163). Opening by
            # engine also keeps first-time incremental indexing of a brand-new
            # repo working (its row doesn't exist yet, so a root lookup would
            # fail).
            from entrygraph.db.engine import make_engine
            from entrygraph.db.meta import check_schema

            engine = make_engine(db)
            check_schema(engine)
            try:
                return index_repository(
                    root,
                    engine,
                    incremental=True,
                    paranoid=args.paranoid,
                    include_tests=args.include_tests,
                )
            finally:
                engine.dispose()

        if args.json:
            print(to_json(_run()))
            return 0

        mode = "full re-index" if getattr(args, "full", False) else "index"
        with con.status(f"[bold]Running {mode}[/] on [cyan]{root}[/]…", spinner="dots"):
            stats = _run()

    body = Text()
    body.append("files    ", style="bold")
    body.append(f"{stats.files_indexed} indexed", style="green")
    body.append(
        f", {stats.files_skipped} skipped, {stats.files_deleted} deleted "
        f"of {stats.files_scanned} scanned\n",
        style="dim",
    )
    body.append("graph    ", style="bold")
    body.append(f"{stats.symbols} ", style="cyan")
    body.append("symbols  ", style="dim")
    body.append(f"{stats.edges} ", style="cyan")
    body.append("edges  ", style="dim")
    body.append(f"{stats.entrypoints} ", style="cyan")
    body.append("entrypoints\n", style="dim")
    body.append("db       ", style="bold")
    body.append(f"{db}", style="")
    con.print(
        Panel(
            body,
            title=f"[bold green]✓[/] indexed [cyan]{root.name}[/]",
            subtitle=f"[dim]{stats.duration_seconds}s[/]",
            border_style="green",
            expand=False,
        )
    )
    return 0


def cmd_detect(args) -> int:
    with _open(args) as graph:
        report = graph.detect()
    if args.json:
        print(to_json(report))
        return 0
    con = console()

    coverage = _catalog_coverage()
    langs = render.table("Languages")
    langs.add_column("LANGUAGE", style="bold")
    langs.add_column("FILES", justify="right")
    langs.add_column("SHARE")
    langs.add_column("TAINT CATALOG", style="dim")
    for lang in report.languages:
        langs.add_row(
            lang.name,
            str(lang.file_count),
            _percent_bar(lang.percent),
            _coverage_cell(coverage.get(lang.name)),
        )
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
        rows = graph.symbols(
            kind=args.kind, name=args.name, qname=args.qname, file=args.file, limit=args.limit
        )
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=True)
    return 0


def cmd_entrypoints(args) -> int:
    with _open(args) as graph:
        rows = graph.entrypoints(
            kind=args.kind, framework=args.framework, route=args.route, limit=args.limit
        )
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
        tbl.add_row(
            entrypoint_kind_text(r.kind),
            render.cell(r.framework),
            method_text(r.http_method),
            render.cell(r.route),
            r.symbol.qname,
        )
    con.print(tbl)
    return 0


def cmd_callers(args) -> int:
    with _open(args) as graph:
        rows = graph.callers(
            args.qname, depth=args.depth, include_speculative=args.include_speculative
        )
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=False)
    return 0


def cmd_callees(args) -> int:
    with _open(args) as graph:
        rows = graph.callees(
            args.qname, depth=args.depth, include_speculative=args.include_speculative
        )
    if args.json:
        print(to_json(rows))
    else:
        _print_symbol_table(rows, with_line=False)
    return 0


def cmd_references(args) -> int:
    """Every call site targeting a symbol — the caller, its file:line, and the
    edge confidence. Unlike `callers` (which lists distinct caller symbols), this
    lists each individual reference with its location, so a result is checkable."""
    with _open(args) as graph:
        refs = graph.references(args.qname)
    if args.json:
        print(to_json(refs))
        return 0
    con = console()
    if not refs:
        con.print("[dim](no references)[/]")
        return 0
    tbl = render.table(caption=f"[dim]{len(refs)} reference(s)[/]")
    tbl.add_column("CALLER", style="bold", overflow="fold")
    tbl.add_column("LOCATION", style="cyan", no_wrap=True)
    tbl.add_column("CONFIDENCE", no_wrap=True)
    for r in sorted(refs, key=lambda e: (e.src_qname, e.line)):
        tbl.add_row(r.src_qname, _loc(r.file, r.line) or "?", confidence_text(r.confidence))
    con.print(tbl)
    return 0


def _display_name(sym) -> str:
    """A short, readable symbol name for a path row: `subprocess.run` for externals,
    `ReportRunner.start` for methods, the bare name otherwise (the file:line column
    carries the location, so the dotted module prefix is redundant)."""
    qname = sym.qname
    if ":" in qname:  # external placeholder, e.g. py:subprocess.run / rb:*.execute
        return qname.split(":", 1)[1]
    parts = qname.split(".")
    if sym.kind == "method" and len(parts) >= 2:
        return ".".join(parts[-2:])
    return parts[-1]


def _loc(file: str | None, line: int) -> str | None:
    return f"{file}:{line}" if file else None


_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _entrypoint_label(ep) -> str:
    """A short, human label for the entrypoint a path is reachable through:
    `POST /reports` for a route, otherwise the command/handler name."""
    if ep.route:
        method = (ep.http_method or "").strip()
        return f"{method} {ep.route}".strip()
    return _display_name(ep.symbol)


def _confidence_phrase(min_conf: int) -> str:
    """The weakest link on the path, in plain terms (drives whether to trust it)."""
    if min_conf >= int(Confidence.IMPORT):
        return "resolved — every call is exact/import"
    if min_conf >= int(Confidence.FUZZY):
        return "fuzzy — the weakest call is a unique-name guess"
    return "unresolved — the weakest call is a wildcard/dynamic guess"


def _verdict(path) -> tuple[str, str]:
    """(text, style) for the finding's headline: fuses the flow verdict with the
    sink's severity so a reader knows at a glance whether to act."""
    sev = path.severity or "unknown"
    cat = path.sink_category or "sink"
    tail = f"{sev}-severity {cat} sink"
    verified = getattr(path, "taint_verified", None)
    if verified is True:
        return f"confirmed data flow → {tail}", _SEVERITY_STYLE.get(path.severity, "red")
    if verified is False:
        return f"reachable, but no data flow observed → {tail}", "yellow"
    return f"reachable → {tail}", "yellow"


def _line_reader(repo_root: str | None):
    """Best-effort reader of the literal source line at file:line, given the indexed
    repo root. Caches each file's lines; returns None when the repo/file/line is gone
    (querying a `.db` whose repo has moved must not crash — just skip the snippet)."""
    if not repo_root:
        return lambda _file, _line: None
    root = Path(repo_root)
    cache: dict[str, list[str] | None] = {}

    def read(file: str | None, line: int | None) -> str | None:
        if not file or not line or line < 1:
            return None
        if file not in cache:
            try:
                cache[file] = (
                    (root / file).read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError:
                cache[file] = None
        lines = cache[file]
        if not lines or line > len(lines):
            return None
        text = lines[line - 1].strip()
        # generous cap so real code lines pass through whole (they word-wrap in the
        # card); only bounds pathological minified lines.
        return (text[:399] + "…") if len(text) > 400 else text

    return read


def _path_card(index: int, path, read_line=None, entrypoint=None) -> Group:
    """A finding card, read top-down:

    [1] <verdict — flow + severity, so you know whether to act>
      entrypoint  <the route/command this is reachable through>
      source      <where untrusted input enters>   file:line
                  <the literal source line>
        ↓         <each call on the way to the sink>  file:line
      sink        <the dangerous call>               file:line
                  <the literal sink line>
      confidence  <how trustworthy the weakest link is>
    """
    syms, edges = path.symbols, path.edges
    read_line = read_line or (lambda _f, _l: None)

    # --- headline verdict ---
    verdict_text, verdict_style = _verdict(path)
    head = Text()
    head.append(f"[{index}] ", style="dim")
    head.append(verdict_text, style=f"bold {verdict_style}")
    lines: list = [head]

    # rows: (label, name, location, annotation, snippet)
    src_file = getattr(syms[0], "file", None)
    # a module-level route source (whole Grape/Rails file) has no meaningful line
    src_loc = src_file if syms[0].kind == "module" else _loc(src_file, syms[0].start_line)

    # what the source reads, in plain words (channel + key when we proved a read)
    src_ann = Text()
    channel, key = path.source_channel, path.source_key
    if channel and key:
        src_ann.append(f'{channel} "{key}"', style="cyan")
    elif channel:
        src_ann.append(channel, style="cyan")
    elif path.source_kind in ("handler", "handler_params"):
        src_ann.append("input via handler (no explicit read)", style="dim")

    rows: list[tuple[str, str, str | None, Text, str | None]] = []
    if entrypoint is not None:
        ep_ann = Text(
            f"{entrypoint.framework or ''} {entrypoint.kind}".strip(), style="dim magenta"
        )
        rows.append(("entrypoint", _entrypoint_label(entrypoint), None, ep_ann, None))
    src_snip = None if syms[0].kind == "module" else read_line(src_file, syms[0].start_line)
    rows.append(("source", _display_name(syms[0]), src_loc, src_ann, src_snip))

    for i, edge in enumerate(edges):
        is_sink = i == len(edges) - 1
        loc = _loc(getattr(syms[i], "file", None), edge.line)
        ann = Text()
        # only flag the uncertain links; a resolved (exact/import) hop stays clean
        if edge.confidence <= int(Confidence.FUZZY):
            ann.append(f"~ {confidence_text(edge.confidence).plain} (guess)", style="yellow")
        if is_sink and edge.constant_args:
            ann.append("  literal args", style="dim green")
        snip = read_line(getattr(syms[i], "file", None), edge.line) if is_sink else None
        rows.append(("sink" if is_sink else "↓", _display_name(syms[i + 1]), loc, ann, snip))

    name_w = max(len(r[1]) for r in rows)
    for label, name, loc, ann, snip in rows:
        line = Text("  ", no_wrap=True, overflow="ellipsis")
        is_step = label == "↓"
        line.append(f"{label:<10} " if not is_step else "    ↓      ", style=_row_style(label))
        name_style = "bold red" if label == "sink" else "bold" if label == "source" else "white"
        line.append(name.ljust(name_w), style=name_style)
        line.append("  ")
        line.append(loc or "", style="cyan" if loc else "dim")
        if ann.plain:
            line.append("  ")
            line.append_text(ann)
        lines.append(line)
        if snip:
            style = "italic red" if label == "sink" else "dim italic"
            lines.append(Padding(Text(snip, style=style), (0, 0, 0, 13), expand=False))

    conf = Text("  ")
    conf.append(f"{'confidence':<10} ", style="dim")
    conf.append(_confidence_phrase(path.min_confidence), style="dim")
    lines.append(conf)
    if path.may_continue:
        lines.append(
            Text("             path may continue past excluded/dynamic edges", style="dim")
        )
    return Group(*lines)


def _row_style(label: str) -> str:
    return "bold" if label in ("source", "sink", "entrypoint") else "dim"


def cmd_paths(args) -> int:
    if getattr(args, "list_categories", False):
        with _open(args) as graph:
            sources = graph.source_categories()
            sinks = graph.sink_categories()
        if args.json:
            print(to_json({"source_categories": sources, "sink_categories": sinks}))
            return 0
        con = console()
        con.print(f"[bold]source categories[/]  [dim]{', '.join(sources) or '—'}[/]")
        con.print(f"[bold]sink categories[/]    [dim]{', '.join(sinks) or '—'}[/]")
        con.print("[dim]pass 'all' to either to match every tagged source/sink[/]")
        return 0
    if not args.source and not args.source_category:
        raise EntrygraphError("provide --source and/or --source-category")
    # A missing sink would leave the sink set empty and print "no paths found",
    # which reads like a clean result — require an explicit sink so an incomplete
    # query can't be mistaken for "no reachable sinks".
    if not args.sink and not args.sink_category:
        raise EntrygraphError("provide --sink and/or --sink-category")
    with _open(args) as graph:
        paths = graph.paths(
            source=args.source,
            source_category=args.source_category,
            sink=args.sink,
            sink_category=args.sink_category,
            max_depth=args.max_depth,
            max_paths=args.max_paths,
            min_confidence=args.min_confidence,
            include_fuzzy=args.include_fuzzy,
            include_unresolved=args.include_unresolved,
            include_callbacks=args.include_callbacks,
            explicit_sources=args.explicit_sources,
            confirmed_only=args.confirmed_only,
            taint_hops=args.taint_hops,
            strict=args.strict,
        )
        read_line = _line_reader(graph.repo_root)  # read original lines while db is open-adjacent
        coverage_note = _thin_coverage_note(graph.detect().languages, len(paths))
        # map each path's source symbol to the entrypoint it's reachable through
        # (the HTTP route / CLI command an attacker would actually hit), when any
        ep_by_symbol = {ep.symbol.id: ep for ep in graph.entrypoints()}
    if coverage_note:
        print(coverage_note, file=sys.stderr)
    if getattr(paths, "mode", None) == "widened":
        # The adaptive search found no high-confidence paths and fell back to the
        # speculative frontier (class-hierarchy, unresolved wildcard sinks, callbacks)
        # — common on large/dynamic codebases. Say so, since these are lower-confidence.
        print(
            "note: no high-confidence paths; widened to the speculative frontier "
            "(fuzzy/unresolved/callback edges — lower confidence). Use --strict to disable.",
            file=sys.stderr,
        )
    truncated = bool(getattr(paths, "truncated", False))
    if truncated:
        # A budget-truncated search may have missed paths — never let an empty or
        # short result read as a clean "no reachable sinks" on a large graph.
        print(
            "warning: path search hit its work budget and may be incomplete; "
            "narrow --source/--sink or lower --max-depth for a complete answer.",
            file=sys.stderr,
        )
    if args.json:
        print(
            to_json(
                [
                    {
                        "length": len(p.symbols),
                        "min_confidence": p.min_confidence,
                        "severity": p.severity,
                        "sink_category": p.sink_category,
                        "may_continue": p.may_continue,
                        "source_kind": p.source_kind,
                        "taint_verified": p.taint_verified,
                        "source_channel": p.source_channel,
                        "source_key": p.source_key,
                        "entrypoint": (
                            {
                                "kind": ep.kind,
                                "framework": ep.framework,
                                "http_method": ep.http_method,
                                "route": ep.route,
                            }
                            if (ep := ep_by_symbol.get(p.symbols[0].id))
                            else None
                        ),
                        "symbols": [s.qname for s in p.symbols],
                        "lines": [e.line for e in p.edges],
                        "source_line": None
                        if p.symbols[0].kind == "module"
                        else read_line(
                            getattr(p.symbols[0], "file", None), p.symbols[0].start_line
                        ),
                        "sink_line": read_line(
                            getattr(p.symbols[-2], "file", None), p.edges[-1].line
                        )
                        if p.edges
                        else None,
                    }
                    for p in paths
                ]
            )
        )
        return 0 if paths else 1

    con = console()
    if not paths:
        con.print(
            Panel("[yellow]No source → sink paths found.[/]", border_style="yellow", expand=False)
        )
        return 1
    origin = args.source or args.source_category or "source"
    target = args.sink or args.sink_category or "sink"
    con.print(f"[bold]{_plural(len(paths), 'path')}[/]  [dim]{origin} → {target}[/]\n")
    for i, path in enumerate(paths, 1):
        con.print(_path_card(i, path, read_line, entrypoint=ep_by_symbol.get(path.symbols[0].id)))
        con.print()
    con.print(
        "[dim]a finding is a lead to review, not proof of a bug — open the file:line "
        "and read the code[/]"
    )
    return 0


def cmd_stats(args) -> int:
    with _open(args) as graph:
        stats = graph.stats()
        languages = graph.detect().languages
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
    coverage = _catalog_coverage()
    parts = []
    for lang in languages:
        cov = coverage.get(lang.name)
        parts.append(f"{lang.name} {cov.tier if cov else 'none'}")
    if parts:
        con.print(f"[dim]taint catalog: {' · '.join(parts)}[/]")
    return 0


def cmd_repos(args) -> int:
    """List the repositories indexed in the database. In the global multi-repo DB,
    this is how you see which `--repo` values are valid."""
    db = _discover_db(getattr(args, "db", None))
    if not db.exists():
        if args.json:
            print(to_json([]))
        else:
            console().print("[dim](no indexed repositories)[/]")
        return 0
    repos = CodeGraph.list_repos(db)
    if args.json:
        print(to_json(repos))
        return 0
    con = console()
    if not repos:
        con.print("[dim](no indexed repositories)[/]")
        return 0
    tbl = render.table(caption=f"[dim]{len(repos)} repositor{'y' if len(repos) == 1 else 'ies'}[/]")
    tbl.add_column("NAME", style="bold")
    tbl.add_column("ROOT", style="cyan", overflow="fold")
    tbl.add_column("FILES", justify="right")
    tbl.add_column("SYMBOLS", justify="right")
    for r in repos:
        tbl.add_row(r.name, r.root, str(r.files), str(r.symbols))
    con.print(tbl)
    return 0


# ---------------- parser ----------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="entrygraph", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"entrygraph {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p):
        p.add_argument("--db", help="index database path (default: ~/.entrygraph/.entrygraph.db)")
        p.add_argument(
            "--repo",
            help="in a multi-repo DB, select the repository by root path or name "
            "(default: the repo containing the working directory); see `entrygraph repos`",
        )
        p.add_argument("--json", action="store_true", help="emit JSON")

    p = sub.add_parser("index", help="index or re-index a repository (local path or git URL)")
    p.add_argument("path", help="local directory, or a git URL to clone and index")
    p.add_argument("--db", help="database path (default: ~/.entrygraph/.entrygraph.db)")
    p.add_argument("--full", action="store_true", help="force full re-index (default: incremental)")
    p.add_argument("--paranoid", action="store_true", help="hash every file (skip mtime fast path)")
    p.add_argument(
        "--include-tests",
        action="store_true",
        help="index test files too (default: recorded but excluded; flipping this needs --full)",
    )
    # git-URL options (ignored when `path` is a local directory)
    p.add_argument("--ref", help="branch, tag, or commit to check out when path is a git URL")
    p.add_argument(
        "--depth",
        type=int,
        default=1,
        help="git clone depth for a URL (default 1; 0 = full history)",
    )
    p.add_argument(
        "--full-clone",
        dest="full_clone",
        action="store_true",
        help="clone full git history (equivalent to --depth 0)",
    )
    p.add_argument(
        "--clone-dir",
        dest="clone_dir",
        help="where to place a URL checkout (default: ./.entrygraph/clones/<host>/<org>/<repo>)",
    )
    p.add_argument(
        "--ephemeral",
        action="store_true",
        help="clone a URL to a temp dir and delete it after indexing (no paths snippets afterward)",
    )
    p.add_argument(
        "--timeout", type=int, default=600, help="max seconds for a git clone/fetch (URL only)"
    )
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
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_entrypoints)

    speculative_help = (
        "also include speculative edges: class-hierarchy (CHA) guesses and "
        "unresolved wildcard/dynamic calls (lower confidence, off by default)"
    )
    p = sub.add_parser("callers", help="who calls this symbol")
    add_db(p)
    p.add_argument("qname")
    p.add_argument("--depth", type=int, default=1)
    p.add_argument(
        "--include-speculative",
        dest="include_speculative",
        action="store_true",
        help=speculative_help,
    )
    p.set_defaults(func=cmd_callers)

    p = sub.add_parser("callees", help="what this symbol calls")
    add_db(p)
    p.add_argument("qname")
    p.add_argument("--depth", type=int, default=1)
    p.add_argument(
        "--include-speculative",
        dest="include_speculative",
        action="store_true",
        help=speculative_help,
    )
    p.set_defaults(func=cmd_callees)

    p = sub.add_parser("references", help="every call site targeting a symbol, with file:line")
    add_db(p)
    p.add_argument("qname")
    p.set_defaults(func=cmd_references)

    p = sub.add_parser("paths", help="source -> sink call paths")
    add_db(p)
    p.add_argument("--source", help="qname or glob")
    p.add_argument(
        "--source-category",
        dest="source_category",
        help="named taint-source category (e.g. http_input, env_input) or 'all'; "
        "run --list-categories to see the valid set",
    )
    p.add_argument(
        "--sink",
        help="qname or glob; the language prefix is optional (subprocess.run "
        "resolves to py:subprocess.run)",
    )
    p.add_argument(
        "--sink-category",
        dest="sink_category",
        help="named sink category (e.g. command_exec, sql) or 'all' for any tagged "
        "sink; run --list-categories to see the valid set",
    )
    p.add_argument(
        "--list-categories",
        dest="list_categories",
        action="store_true",
        help="print the valid source and sink category names for this index and exit",
    )
    p.add_argument("--max-depth", dest="max_depth", type=int, default=25)
    p.add_argument("--max-paths", dest="max_paths", type=int, default=10)
    p.add_argument(
        "--min-confidence",
        dest="min_confidence",
        type=int,
        default=None,
        help="explicit confidence floor (overrides --include-* flags)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="only report high-confidence (resolved) paths; disable the adaptive "
        "fallback that widens to the speculative frontier when none are found",
    )
    # The search is adaptive by default (precise first, widen automatically if empty),
    # so these are rarely needed; each forces exactly that frontier for one run.
    p.add_argument(
        "--include-fuzzy",
        dest="include_fuzzy",
        action="store_true",
        help="force traversal of speculative class-hierarchy (CHA) edges",
    )
    p.add_argument(
        "--include-unresolved",
        dest="include_unresolved",
        action="store_true",
        help="force traversal of unresolved wildcard-sink and dynamic-call edges",
    )
    p.add_argument(
        "--include-callbacks",
        dest="include_callbacks",
        action="store_true",
        help="force following function/method values passed as arguments "
        "(handler registrations, callbacks)",
    )
    p.add_argument(
        "--explicit-sources",
        dest="explicit_sources",
        action="store_true",
        help="only count catalog request-accessor call sites as sources; drop "
        "handler-as-source seeds (handlers with no proven request read)",
    )
    p.add_argument(
        "--confirmed-only",
        dest="confirmed_only",
        action="store_true",
        help="keep only paths where the taint reaching check confirms a request "
        "value flows to the sink (drops unverified and refuted paths)",
    )
    p.add_argument(
        "--taint-hops",
        dest="taint_hops",
        type=int,
        default=5,
        help="max interior call hops the taint reaching check follows "
        "(0 = same-function only; default 5)",
    )
    p.set_defaults(func=cmd_paths)

    p = sub.add_parser("stats", help="index statistics")
    add_db(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("repos", help="list the repositories indexed in the database")
    p.add_argument("--db", help="index database path (default: ~/.entrygraph/.entrygraph.db)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.set_defaults(func=cmd_repos)

    # Unified web app (API + SPA); registered lazily — the module is light and
    # its heavy imports (fastapi/uvicorn) stay inside the command handlers.
    from entrygraph.server.cli import register as register_server

    register_server(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    import os

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EntrygraphError as exc:
        console(stderr=True).print(Text(f"error: {exc}", style="bold red"))
        return 2
    except KeyboardInterrupt:
        console(stderr=True).print(Text("interrupted", style="bold red"))
        return 130
    except Exception as exc:  # noqa: BLE001 — top-level CLI guard, keep output clean
        # An unexpected failure (e.g. a DB integrity error while indexing) should
        # surface as a concise diagnostic and a non-zero exit, not a raw traceback.
        # Set ENTRYGRAPH_DEBUG=1 to re-raise the full traceback while developing.
        if os.environ.get("ENTRYGRAPH_DEBUG"):
            raise
        console(stderr=True).print(Text(f"error: {type(exc).__name__}: {exc}", style="bold red"))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
