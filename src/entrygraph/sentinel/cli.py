"""``entrygraph sentinel`` operational commands (#126).

Run and operate a Sentinel deployment from the CLI: ``serve`` the web process,
run a ``worker``, print the resolved ``config``, list ``installations``, ``purge``
old scans, or seed a ``baseline`` from a local checkout.

Module-level imports are kept light (stdlib only) so registering these commands
costs nothing for core CLI users; every Sentinel import is lazy inside a handler.
The store-backed commands (config/installations/purge/baseline) need only the
core dependencies; ``serve``/``worker`` require the optional ``sentinel`` extra
(uvicorn / arq) and fail with a clear message when it is absent.
"""

from __future__ import annotations

import json
import os

_MISSING_EXTRA = (
    "this command needs the optional Sentinel extra: pip install 'entrygraph[sentinel]'"
)
_DEFAULT_DB = "sqlite:///sentinel.db"


def register(sub) -> None:
    """Add the ``sentinel`` subcommand tree to the top-level CLI subparsers."""
    p = sub.add_parser("sentinel", help="run and operate the Sentinel service (#126)")
    ssub = p.add_subparsers(dest="sentinel_command", required=True)

    sv = ssub.add_parser("serve", help="run the web process (webhook + REST API + dashboard)")
    # binding all interfaces is the operator's explicit choice for a server
    sv.add_argument("--host", default="0.0.0.0")  # nosec B104
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_serve)

    wk = ssub.add_parser("worker", help="run the scan worker (arq)")
    wk.set_defaults(func=_worker)

    cf = ssub.add_parser("config", help="print the resolved config (secrets redacted)")
    cf.set_defaults(func=_config)

    ls = ssub.add_parser("installations", help="list installations in the store")
    _add_db(ls)
    ls.add_argument("--json", action="store_true", help="machine-readable output")
    ls.set_defaults(func=_installations)

    pu = ssub.add_parser("purge", help="delete all but the newest N scans per repo")
    _add_db(pu)
    pu.add_argument("--keep", type=int, default=50, help="scan runs to keep per repo")
    pu.add_argument("--installation", type=int, help="limit to one installation id")
    pu.set_defaults(func=_purge)

    bl = ssub.add_parser("baseline", help="cut a repo's baseline from a local checkout")
    _add_db(bl)
    bl.add_argument("path", help="local checkout to index")
    bl.add_argument("--installation", type=int, required=True, help="installation id")
    bl.add_argument("--repo", required=True, help="owner/name")
    bl.add_argument("--branch", default="main", help="branch the baseline represents")
    bl.set_defaults(func=_baseline)


def _add_db(p) -> None:
    p.add_argument(
        "--database-url",
        default=os.environ.get("SENTINEL_DATABASE_URL", _DEFAULT_DB),
        help="findings store URL (env: SENTINEL_DATABASE_URL; default sqlite)",
    )


def _open_store(args):
    from entrygraph.sentinel import store

    return store, store.init_store(store.make_store_engine(args.database_url))


# ---------------- serve / worker (need the sentinel extra) ----------------


def _serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print(_MISSING_EXTRA)
        return 2
    # validate config up front so a misconfig fails fast, not mid-request
    from entrygraph.sentinel.config import ConfigError, SentinelConfig

    try:
        SentinelConfig.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    uvicorn.run(
        "entrygraph.sentinel.app:build_from_env",
        factory=True,
        host=args.host,
        port=args.port,
    )
    return 0


def _worker(args) -> int:
    try:
        from arq import run_worker
        from arq.connections import RedisSettings
    except ImportError:
        print(_MISSING_EXTRA)
        return 2
    from entrygraph.sentinel.config import ConfigError, SentinelConfig
    from entrygraph.sentinel.queue import WorkerSettings

    try:
        config = SentinelConfig.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    run_worker(WorkerSettings, redis_settings=RedisSettings.from_dsn(config.redis_url))
    return 0


# ---------------- store-backed commands (core deps only) ----------------


def _config(_args) -> int:
    from entrygraph.sentinel.config import ConfigError, SentinelConfig

    try:
        config = SentinelConfig.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    print(json.dumps(config.redacted(), indent=2))
    return 0


def _installations(args) -> int:
    store, session_factory = _open_store(args)
    with session_factory() as session:
        rows = [
            {
                "id": i.id,
                "account_login": i.account_login,
                "suspended": i.suspended,
                "repos": store.repo_count(session, i.id),
            }
            for i in store.list_installations(session)
        ]
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("no installations")
    else:
        for r in rows:
            flag = " [suspended]" if r["suspended"] else ""
            print(f"{r['id']:>12}  {r['account_login']:<28} {r['repos']} repo(s){flag}")
    return 0


def _purge(args) -> int:
    store, session_factory = _open_store(args)
    total = 0
    with session_factory() as session:
        ids = (
            [args.installation]
            if args.installation is not None
            else [i.id for i in store.list_installations(session)]
        )
        for iid in ids:
            for repo_id in store.installation_repo_ids(session, iid):
                total += store.purge_scans(session, repo_id, keep=args.keep)
    print(f"purged {total} scan run(s); kept the newest {args.keep} per repo")
    return 0


def _baseline(args) -> int:
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from entrygraph.api import CodeGraph
    from entrygraph.gate import store as gate_store

    store, session_factory = _open_store(args)
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="sentinel-baseline-") as tmp:
        graph = CodeGraph.index(Path(args.path), db=Path(tmp) / "graph.db")
        try:
            with session_factory() as session:
                owner = args.repo.split("/", 1)[0]
                store.ensure_installation(session, args.installation, owner, now=now)
                repo_id = store.resolve_repo(session, args.installation, args.repo, now=now)
                policy = gate_store.get_policy(session, repo_id)
                findings = gate_store.enumerate_findings(graph, policy)
                count = gate_store.save_baseline(
                    session, repo_id, findings, branch=args.branch, now=now
                )
        finally:
            graph.close()
    print(f"cut baseline for {args.repo} ({args.branch}): {count} path(s)")
    return 0
