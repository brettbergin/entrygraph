"""Rails routes deep parse (RESTful expansion, namespace/scope stacking, to:
targets) and the cross-file route -> controller-action binding pass."""

from __future__ import annotations

from pathlib import Path

from entrygraph import CodeGraph
from entrygraph.detect.entrypoints import rules_for
from entrygraph.detect.rails_draw import resolve_draw_scopes
from entrygraph.detect.rails_link import link_rails
from entrygraph.extract.ir import FileExtraction, RawReference, Span
from entrygraph.kinds import SymbolKind
from entrygraph.resolve.symbol_table import SymbolTable

FIX = Path(__file__).parent / "fixtures"


def _call(name, preview, start, end=None):
    return RawReference(
        kind="call",
        callee_text=name,
        callee_name=name,
        receiver_text=None,
        span=Span(start, 0, end or start, 40),
        caller_qualified_name=None,
        arg_preview=preview,
    )


def _routes_ext(references, path="config/routes.rb"):
    return FileExtraction(
        path=path,
        language="ruby",
        module_path="config.routes",
        parse_ok=True,
        error_count=0,
        symbols=[],
        references=list(references),
    )


def _rails_rule():
    return {r.id: r for r in rules_for("ruby", {"rails"})}["ruby.rails.routes"]


def _match(references, path="config/routes.rb"):
    return _rails_rule().match(_routes_ext(references, path))


def test_resources_expand_restful_set():
    hints = _match([_call("resources", ":posts", 2)])
    got = {(h.metadata["action"], ",".join(h.http_methods), h.route) for h in hints}
    assert got == {
        ("index", "GET", "/posts"),
        ("create", "POST", "/posts"),
        ("new", "GET", "/posts/new"),
        ("edit", "GET", "/posts/:id/edit"),
        ("show", "GET", "/posts/:id"),
        ("update", "PATCH,PUT", "/posts/:id"),
        ("destroy", "DELETE", "/posts/:id"),
    }
    assert all(h.metadata["controller"] == "posts" for h in hints)


def test_resources_only_and_except():
    only = _match([_call("resources", ":posts, only: [:index, :show]", 2)])
    assert {h.metadata["action"] for h in only} == {"index", "show"}
    exc = _match([_call("resources", ":posts, except: [:destroy, :edit, :new]", 2)])
    assert {h.metadata["action"] for h in exc} == {"index", "create", "show", "update"}


def test_singular_resource_has_no_index_or_id():
    hints = _match([_call("resource", ":profile", 2)])
    assert {h.metadata["action"] for h in hints} == {
        "show",
        "create",
        "new",
        "edit",
        "update",
        "destroy",
    }
    assert all(":id" not in (h.route or "") for h in hints)
    assert ("show", "/profile") in {(h.metadata["action"], h.route) for h in hints}


def test_namespace_prefixes_path_and_controller_module():
    hints = _match(
        [
            _call("namespace", ":admin", 2, end=6),
            _call("resources", ":reports, only: [:show]", 3),
        ]
    )
    (show,) = hints
    assert show.route == "/admin/reports/:id"
    assert show.metadata["controller"] == "admin/reports"
    assert [(p.name, p.location) for p in show.parameters] == [("id", "path")]


def test_nested_resources_scope_under_parent_id():
    hints = _match(
        [
            _call("resources", ":posts, only: [:show] do", 2, end=5),
            _call("resources", ":comments, only: [:index]", 3),
        ]
    )
    by_action = {(h.metadata["controller"], h.metadata["action"]): h for h in hints}
    comments = by_action[("comments", "index")]
    assert comments.route == "/posts/:post_id/comments"
    assert {p.name for p in comments.parameters} == {"post_id"}
    # the parent's own routes are NOT nested under the id scope
    assert by_action[("posts", "show")].route == "/posts/:id"


def test_verb_to_target_root_and_hashrocket():
    hints = _match(
        [
            _call("root", "'pages#home'", 2),
            _call("get", "'profile', to: 'users#show'", 3),
            _call("get", "'welcome' => 'welcome#index'", 4),
        ]
    )
    got = {(h.route, h.metadata.get("controller"), h.metadata.get("action")) for h in hints}
    assert got == {
        ("/", "pages", "home"),
        ("/profile", "users", "show"),
        ("/welcome", "welcome", "index"),
    }


def test_match_via_and_shorthand_inference():
    hints = _match(
        [
            _call("match", "'/hook', to: 'hooks#receive', via: [:get, :post]", 2),
            _call("get", "'welcome/index'", 3),
            _call("get", "'/health'", 4),  # single segment: no target inferred
        ]
    )
    by_route = {h.route: h for h in hints}
    assert by_route["/hook"].http_methods == ["GET", "POST"]
    assert by_route["/hook"].metadata["controller"] == "hooks"
    assert by_route["/welcome/index"].metadata["controller"] == "welcome"
    assert by_route["/welcome/index"].metadata["action"] == "index"
    assert "controller" not in by_route["/health"].metadata


def test_scope_module_and_path():
    hints = _match(
        [
            _call("scope", "'/v1', module: 'api' do", 2, end=5),
            _call("get", "'status', to: 'health#status'", 3),
        ]
    )
    (status,) = hints
    assert status.route == "/v1/status"
    assert status.metadata["controller"] == "api/health"


def test_scope_module_symbol_form():
    hints = _match(
        [
            _call(
                "scope",
                "path: ':project_id', constraints: { id: /\\d+/ }, module: :projects",
                2,
                end=5,
            ),
            _call("resources", ":milestones, only: [:index]", 3),
        ]
    )
    (index,) = hints
    assert index.route == "/:project_id/milestones"
    assert index.metadata["controller"] == "projects/milestones"


def test_non_routes_files_ignored():
    assert _match([_call("get", "'/x'", 1)], path="app/models/post.rb") == []


# ---------------- cross-file draw() scope inheritance ----------------


def _draw_scopes(files: dict[str, list]):
    """files: routes-file path -> its calls; returns resolve_draw_scopes' output."""
    return resolve_draw_scopes(
        [(path, _routes_ext(calls, path), False) for path, calls in files.items()]
    )


def test_draw_child_inherits_enclosing_scope():
    scopes = _draw_scopes(
        {
            "config/routes.rb": [
                _call("scope", "path: 'admin', module: :admin", 2, end=4),
                _call("draw", ":reports", 3),
            ],
            "config/routes/reports.rb": [_call("resources", ":reports", 1)],
        }
    )
    assert scopes == {"config/routes/reports.rb": [("admin", None), (None, "admin")]}


def test_draw_scopes_compose_through_a_chain():
    scopes = _draw_scopes(
        {
            "config/routes.rb": [
                _call("scope", "path: '*namespace_id'", 2, end=6),
                _call("scope", "path: ':project_id', module: :projects", 3, end=5),
                _call("draw", ":project", 4),
            ],
            "config/routes/project.rb": [
                _call("scope", "'-'", 2, end=4),
                _call("draw", ":repository", 3),
            ],
            "config/routes/repository.rb": [_call("resources", ":branches, only: [:index]", 1)],
        }
    )
    assert scopes["config/routes/repository.rb"] == [
        ("*namespace_id", None),
        (":project_id", None),
        (None, "projects"),
        ("-", None),
    ]
    # and the child file's routes carry the whole inherited prefix
    child = _routes_ext(
        [_call("resources", ":branches, only: [:index]", 1)], "config/routes/repository.rb"
    )
    child.rails_draw_scopes = scopes[child.path]
    (index,) = _rails_rule().match(child)
    assert index.route == "/*namespace_id/:project_id/-/branches"
    assert index.metadata["controller"] == "projects/branches"


def test_draw_from_conflicting_scopes_stays_unseeded():
    scopes = _draw_scopes(
        {
            "config/routes.rb": [
                _call("scope", "path: 'a', module: :a", 2, end=4),
                _call("draw", ":shared", 3),
                _call("scope", "path: 'b', module: :b", 5, end=7),
                _call("draw", ":shared", 6),
            ],
            "config/routes/shared.rb": [_call("get", "'/x', to: 'x#y'", 1)],
        }
    )
    assert scopes == {}


def test_draw_cycle_and_unscoped_draw_seed_nothing():
    scopes = _draw_scopes(
        {
            "config/routes.rb": [_call("draw", ":api", 2)],  # no enclosing scope
            "config/routes/api.rb": [_call("draw", ":api", 1)],  # draws itself
        }
    )
    assert scopes == {}


# ---------------- cross-file binding pass ----------------


def _table(*symbols):
    table = SymbolTable()
    for sid, (qname, kind) in enumerate(symbols, start=1):
        table.add_symbol(sid, qname, qname.rsplit(".", 1)[-1], kind, "ruby", None)
    return table


def test_link_rails_binds_controller_action():
    ext = _routes_ext([_call("get", "'/posts/:id', to: 'posts#show'", 2)])
    ext.entrypoint_hints = _rails_rule().match(ext)
    table = _table(("controllers.posts_controller.PostsController.show", SymbolKind.METHOD))

    assert link_rails([("config/routes.rb", ext, False)], table) == 1
    (hint,) = ext.entrypoint_hints
    assert hint.handler_qualified_name == "controllers.posts_controller.PostsController.show"


def test_link_rails_namespace_disambiguates():
    ext = _routes_ext(
        [
            _call("namespace", ":admin", 2, end=4),
            _call("resources", ":reports, only: [:show]", 3),
        ]
    )
    ext.entrypoint_hints = _rails_rule().match(ext)
    table = _table(
        ("controllers.reports_controller.ReportsController.show", SymbolKind.METHOD),
        ("controllers.admin.reports_controller.ReportsController.show", SymbolKind.METHOD),
    )

    assert link_rails([("config/routes.rb", ext, False)], table) == 1
    (hint,) = ext.entrypoint_hints
    assert hint.handler_qualified_name == (
        "controllers.admin.reports_controller.ReportsController.show"
    )


def test_link_rails_ambiguous_stays_unbound():
    ext = _routes_ext([_call("get", "'/a', to: 'posts#show'", 2)])
    ext.entrypoint_hints = _rails_rule().match(ext)
    table = _table(
        ("pkg_a.PostsController.show", SymbolKind.METHOD),
        ("pkg_b.PostsController.show", SymbolKind.METHOD),
    )

    assert link_rails([("config/routes.rb", ext, False)], table) == 0
    assert ext.entrypoint_hints[0].handler_qualified_name is None


# ---------------- end to end on the fixture app ----------------


def test_rails_app_end_to_end(tmp_path):
    g = CodeGraph.index(FIX / "ruby" / "rails_app", db=tmp_path / "rails.db")
    try:
        eps = g.entrypoints(kind="http_route", framework="rails")
        by = {(e.http_method, e.route): e for e in eps}

        show = by[("GET", "/posts/:id")]
        assert show.symbol.qname.endswith("PostsController.show")
        assert [(p.name, p.location, p.provenance) for p in show.parameters] == [
            ("id", "path", "route")
        ]

        assert by[("GET", "/admin/reports/:id")].symbol.qname.endswith(
            "Admin.ReportsController.show"
        )
        assert by[("GET", "/profile")].symbol.qname.endswith("UsersController.show")
        assert by[("GET", "/")].symbol.qname.endswith("PagesController.home")
        # split routes file (config/routes/api.rb) is part of the route surface
        assert by[("GET", "/api/ping")].symbol.qname.endswith("ApiController.ping")

        # a file drawn inside `scope(path: 'v2', ..., module: :v2)` inherits both
        # the path prefix and the controller module across the file boundary
        assert by[("GET", "/v2/widgets")].symbol.qname.endswith("V2::WidgetsController.index")

        comments = by[("GET", "/posts/:post_id/comments")]
        assert comments.symbol.qname.endswith("CommentsController.index")
        assert {p.name for p in comments.parameters} == {"post_id"}

        # strong params: create's post_params helper contributes permit keys
        create = by[("POST", "/posts")]
        assert {(p.name, p.location, p.provenance) for p in create.parameters} == {
            ("title", "body", "strong_params"),
            ("body", "body", "strong_params"),
        }

        # usage read with no matching route segment surfaces as a usage param
        profile = by[("GET", "/profile")]
        assert {(p.name, p.provenance, p.location) for p in profile.parameters} == {
            ("id", "usage", "query")
        }

        # a usage read that matches a declared path param does NOT duplicate
        assert [(p.name, p.provenance) for p in show.parameters] == [("id", "route")]

        # the bound controller action is a real taint source: params[:id] -> system
        paths = g.paths(
            source_category="http_input", sink_category="command_exec", include_unresolved=True
        )
        assert any(
            p.symbols[0].qname.endswith("PostsController.show") and p.taint_verified is True
            for p in paths
        )
    finally:
        g.close()


def test_grape_app_end_to_end(tmp_path):
    g = CodeGraph.index(FIX / "ruby" / "grape_app", db=tmp_path / "grape.db")
    try:
        eps = g.entrypoints(kind="http_route", framework="grape")
        by = {(e.http_method, e.route): e for e in eps}

        create = by[("POST", "/users")]
        got = {
            (p.name, p.location, p.required, p.type_ref, p.provenance) for p in create.parameters
        }
        assert got == {
            ("name", "body", True, "String", "dsl"),
            ("age", "body", False, "Integer", "dsl"),
        }

        show = by[("GET", "/users/:id")]
        assert {(p.name, p.provenance, p.location) for p in show.parameters} == {
            ("id", "route", "path"),
            ("verbose", "usage", "query"),
        }
    finally:
        g.close()
