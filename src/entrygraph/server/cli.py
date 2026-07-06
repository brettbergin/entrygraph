"""``entrygraph serve`` — run the unified web app (API + SPA).

Light module-level imports so registering the command is free; FastAPI/uvicorn
load lazily inside the handler. Needs the ``server`` extra.
"""

from __future__ import annotations

_MISSING_EXTRA = "this command needs the optional extra: pip install 'entrygraph[server]'"


def register(sub) -> None:
    p = sub.add_parser("serve", help="serve the unified entrygraph web app")
    p.add_argument("--host", default=None, help="bind host (default EG_HOST or 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="bind port (default EG_PORT or 8100)")
    p.add_argument("--db", default=None, help="graph index path (default EG_DB or global db)")
    p.add_argument("--app-db", default=None, help="app database path (default EG_APP_DB)")
    p.add_argument(
        "--auth",
        choices=("none", "oidc"),
        default=None,
        help="authentication mode (default: oidc when EG_OIDC_ISSUER is set, else none)",
    )
    p.set_defaults(func=_serve)


def _serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print(_MISSING_EXTRA)
        return 2
    import os

    from entrygraph.server.app import create_app
    from entrygraph.server.config import ConfigError, ServerConfig

    # CLI flags override the environment (env stays authoritative for deployments)
    env = dict(os.environ)
    if args.host:
        env["EG_HOST"] = args.host
    if args.port:
        env["EG_PORT"] = str(args.port)
    if args.db:
        env["EG_DB"] = args.db
    if args.app_db:
        env["EG_APP_DB"] = args.app_db
    if args.auth:
        env["EG_AUTH_MODE"] = args.auth

    try:
        config = ServerConfig.from_env(env)
        app = create_app(config)
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2

    mode = "no auth (local dev)" if config.auth_mode == "none" else f"oidc ({config.oidc_issuer})"
    print(f"entrygraph on http://{config.host}:{config.port}  auth: {mode}")
    print(f"  graph db: {config.db_path}")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0
