"""``entrygraph explore`` — serve the read-only graph explorer UI (#explorer).

Light module-level imports so registering the command is free; FastAPI/uvicorn
load lazily inside the handler. ``serve`` needs the ``explore`` extra (uvicorn);
``repos`` only needs the core deps.
"""

from __future__ import annotations

import json

_MISSING_EXTRA = "this command needs the optional extra: pip install 'entrygraph[explore]'"


def register(sub) -> None:
    p = sub.add_parser("explore", help="serve a web UI over an entrygraph index")
    esub = p.add_subparsers(dest="explore_command", required=True)

    sv = esub.add_parser("serve", help="serve the explorer UI + read API")
    sv.add_argument("--db", required=True, help="path to an entrygraph index (.db)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8100)
    sv.set_defaults(func=_serve)

    ls = esub.add_parser("repos", help="list the repos in an index")
    ls.add_argument("--db", required=True, help="path to an entrygraph index (.db)")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_repos)


def _serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print(_MISSING_EXTRA)
        return 2
    from pathlib import Path

    if not Path(args.db).is_file():
        print(f"no index at {args.db}")
        return 2
    from entrygraph.explore.api import create_app

    app = create_app(args.db)
    print(
        "note: `entrygraph explore serve` is deprecated; the unified app "
        "(`entrygraph serve`) supersedes it and will replace it in a future release"
    )
    print(f"explorer on http://{args.host}:{args.port}  (index: {args.db})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _repos(args) -> int:
    from sqlalchemy import select

    from entrygraph.db import models
    from entrygraph.db.engine import make_engine, make_session_factory

    sf = make_session_factory(make_engine(args.db))
    with sf() as s:
        rows = s.execute(select(models.Repository).order_by(models.Repository.root_path)).scalars()
        repos = [
            {"id": r.id, "root_path": r.root_path, "files": r.file_count, "symbols": r.symbol_count}
            for r in rows
        ]
    if args.json:
        print(json.dumps(repos, indent=2))
    elif not repos:
        print("no repos in this index")
    else:
        for r in repos:
            print(f"{r['id']:>6}  {r['root_path']:<50} {r['symbols']} symbols, {r['files']} files")
    return 0
