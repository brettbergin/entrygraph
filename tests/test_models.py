from __future__ import annotations

from sqlalchemy import select

from entrygraph.db.models import Edge, Entrypoint, File, Repository, Symbol
from entrygraph.kinds import Confidence, EdgeKind, EntrypointKind, SymbolKind


def _seed(session) -> None:
    # No relationship() constructs on the models (the bulk-insert writer owns
    # ordering), so flush per dependency level.
    session.add(Repository(id=1, root_path="/repo"))
    session.flush()
    session.add(
        File(
            id=1,
            repo_id=1,
            path="app/main.py",
            language="python",
            content_hash="ab",
            size_bytes=10,
            mtime_ns=1,
            generation=1,
        )
    )
    session.flush()
    session.add_all(
        [
            Symbol(
                id=1,
                repo_id=1,
                file_id=1,
                kind=SymbolKind.FUNCTION,
                name="handler",
                qname="app.main.handler",
                start_line=1,
                end_line=5,
            ),
            Symbol(
                id=2,
                repo_id=1,
                file_id=1,
                kind=SymbolKind.FUNCTION,
                name="helper",
                qname="app.main.helper",
                start_line=7,
                end_line=9,
            ),
            Symbol(
                id=3,
                repo_id=1,
                file_id=None,
                kind=SymbolKind.EXTERNAL,
                name="run",
                qname="py:subprocess.run",
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            Edge(
                id=1,
                repo_id=1,
                kind=EdgeKind.CALLS,
                src_symbol_id=1,
                dst_symbol_id=2,
                dst_qname="app.main.helper",
                src_file_id=1,
                line=3,
                confidence=Confidence.EXACT,
            ),
            Edge(
                id=2,
                repo_id=1,
                kind=EdgeKind.CALLS,
                src_symbol_id=2,
                dst_symbol_id=3,
                dst_qname="py:subprocess.run",
                src_file_id=1,
                line=8,
                confidence=Confidence.IMPORT,
                sink_id="py.command-exec.subprocess",
            ),
        ]
    )
    session.add(
        Entrypoint(
            id=1,
            repo_id=1,
            kind=EntrypointKind.HTTP_ROUTE,
            framework="flask",
            symbol_id=1,
            route="/run",
            http_method="GET",
        )
    )
    session.commit()


def test_round_trip_and_enum_values_stored_as_strings(session_factory):
    with session_factory() as s:
        _seed(s)
        sym = s.execute(select(Symbol).where(Symbol.qname == "app.main.handler")).scalar_one()
        assert sym.kind is SymbolKind.FUNCTION
        # enum persisted by value, not by python name
        raw = s.connection().exec_driver_sql("SELECT kind FROM symbols WHERE id = 1").scalar()
        assert raw == "function"
        raw_ep = (
            s.connection().exec_driver_sql("SELECT kind FROM entrypoints WHERE id = 1").scalar()
        )
        assert raw_ep == "http_route"


def test_delete_file_cascades_symbols_and_nullifies_inbound_edges(session_factory):
    with session_factory() as s:
        _seed(s)
        s.delete(s.get(File, 1))
        s.commit()

        # symbols owned by the file are gone; the external placeholder survives
        assert s.execute(select(Symbol.id)).scalars().all() == [3]
        # edges owned by the file (src_file_id) are gone too
        assert s.execute(select(Edge.id)).scalars().all() == []
        # entrypoints cascaded away with their symbol
        assert s.execute(select(Entrypoint.id)).scalars().all() == []


def test_delete_target_symbol_degrades_edge_to_unresolved(session_factory):
    with session_factory() as s:
        _seed(s)
        s.delete(s.get(Symbol, 3))  # external target of edge 2
        s.commit()

        edge = s.get(Edge, 2)
        assert edge is not None  # edge survives ...
        assert edge.dst_symbol_id is None  # ... degraded to unresolved
        assert edge.dst_qname == "py:subprocess.run"  # textual target retained


def test_edge_via_and_new_kinds_round_trip(session_factory):
    with session_factory() as s:
        _seed(s)
        # a CHA candidate edge and a callback edge, using the new enum members
        s.add_all(
            [
                Edge(
                    id=3,
                    repo_id=1,
                    kind=EdgeKind.CALLS,
                    src_symbol_id=1,
                    dst_symbol_id=2,
                    dst_qname="app.main.helper",
                    src_file_id=1,
                    line=4,
                    confidence=Confidence.FUZZY,
                    via="cha",
                ),
                Edge(
                    id=4,
                    repo_id=1,
                    kind=EdgeKind.PASSED_AS_CALLBACK,
                    src_symbol_id=1,
                    dst_symbol_id=2,
                    dst_qname="app.main.helper",
                    src_file_id=1,
                    line=2,
                    confidence=Confidence.IMPORT,
                ),
            ]
        )
        s.add(
            Entrypoint(
                id=2, repo_id=1, kind=EntrypointKind.MIDDLEWARE, framework="flask", symbol_id=1
            )
        )
        s.commit()

        assert s.get(Edge, 3).via == "cha"
        assert s.get(Edge, 1).via is None  # default for directly-resolved edges
        assert s.get(Edge, 4).kind is EdgeKind.PASSED_AS_CALLBACK
        raw = s.connection().exec_driver_sql("SELECT kind FROM edges WHERE id = 4").scalar()
        assert raw == "callback"
        assert s.get(Entrypoint, 2).kind is EntrypointKind.MIDDLEWARE


def test_graphql_resolver_entrypoint_round_trip(session_factory):
    with session_factory() as s:
        _seed(s)
        s.add(
            Entrypoint(
                id=2,
                repo_id=1,
                kind=EntrypointKind.GRAPHQL_RESOLVER,
                framework="apollo",
                symbol_id=1,
                route="Query.user",
                http_method=None,
            )
        )
        s.commit()

        ep = s.get(Entrypoint, 2)
        assert ep.kind is EntrypointKind.GRAPHQL_RESOLVER
        assert ep.route == "Query.user"
        assert ep.http_method is None
        raw = s.connection().exec_driver_sql("SELECT kind FROM entrypoints WHERE id = 2").scalar()
        assert raw == "graphql_resolver"


def test_graphql_resolver_seeds_http_input_sources():
    from entrygraph.api import _HANDLER_SOURCE_KINDS

    assert EntrypointKind.GRAPHQL_RESOLVER in _HANDLER_SOURCE_KINDS["http_input"]
