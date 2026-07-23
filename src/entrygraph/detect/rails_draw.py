"""Cross-file Rails `draw(:x)` scope inheritance (express_mounts-style pre-pass).

Large Rails apps split `config/routes.rb` into `config/routes/<name>.rb` files
loaded with `draw :name`. Rails evaluates the drawn file's body *at the call
site*, so the child inherits whatever `scope`/`namespace` blocks enclose the
`draw` — GitLab draws `config/routes/repository.rb` from inside
``scope(path: ':project_id', module: :projects)``, which is what makes its
`resources :branches` mean `Projects::BranchesController`.

The routes rule walks one file at a time and cannot see that, so a child file's
targets parsed without the outer module (``branches#index`` instead of
``projects/branches#index``) and the link pass could not resolve them. This pass
runs before the entrypoint rules, walks each routes file's scope stack to the
`draw` calls, and hands each child file the frames it inherits.

Frames compose transitively (routes.rb draws project.rb draws repository.rb). A
child drawn from two places with *different* frames is left unseeded rather than
guessed — a wrong module binds the route to the wrong controller.

Like the Express mount graph, this reads only the current run's extractions: an
incremental run that re-parses a child but not its parent sees no `draw` site and
falls back to the un-inherited parse (route still emitted, handler unbound). The
next full/heal scan restores the binding.
"""

from __future__ import annotations

from entrygraph.detect.entrypoints.ruby import (
    ScopeFrame,
    first_symbol_or_string,
    is_rails_routes_file,
    walk_routes,
)

# `draw :project`, and GitLab's `draw_all :project` (CE + EE variants of one file)
_DRAW_CALLS = frozenset({"draw", "draw_all"})
_ROUTES_DIR = "config/routes"


def _child_path(parent_path: str, name: str) -> str | None:
    """`draw :project` in `<root>/config/routes.rb` -> `<root>/config/routes/project.rb`.

    The drawn name is a path relative to the routes directory, so nested names
    (`draw 'directs/promo'`) work too."""
    idx = parent_path.find(_ROUTES_DIR)
    if idx < 0:
        return None
    return f"{parent_path[:idx]}{_ROUTES_DIR}/{name.strip('/')}.rb"


def resolve_draw_scopes(extractions) -> dict[str, list[ScopeFrame]]:
    """repo-relative routes-file path -> the scope frames it inherits from its
    parent, outermost first. Files that are never drawn (or drawn ambiguously)
    are absent."""
    routes_files = {
        x.path
        for _p, x, _pkg in extractions
        if x.language == "ruby" and is_rails_routes_file(x.path)
    }
    if not routes_files:
        return {}

    # child path -> the frames each `draw` site wraps it in (one entry per site)
    sites: dict[str, list[tuple[str, list[ScopeFrame]]]] = {}
    for _p, x, _pkg in extractions:
        if x.language != "ruby" or x.path not in routes_files:
            continue
        for ref, path_segs, module_segs in walk_routes(x):
            if ref.callee_name not in _DRAW_CALLS:
                continue
            name = first_symbol_or_string(ref.arg_preview)
            child = _child_path(x.path, name) if name else None
            if child is None or child not in routes_files or child == x.path:
                continue
            # zip is wrong here: a frame contributes a path, a module, or both,
            # and the rule only consumes the two sequences, so carry them as one
            # flat frame list that reproduces the same prefixes.
            frames = [(p, None) for p in path_segs] + [(None, m) for m in module_segs]
            sites.setdefault(child, []).append((x.path, frames))

    resolved: dict[str, list[ScopeFrame]] = {}

    def inherited(path: str, stack: frozenset[str]) -> list[ScopeFrame] | None:
        """Frames enclosing `path`'s whole body, or None when it is drawn from
        conflicting places (or is a root file, which inherits nothing)."""
        if path in resolved:
            return resolved[path]
        drawn_from = sites.get(path)
        if not drawn_from or path in stack:  # root file, or a draw cycle
            return None
        first = drawn_from[0][1]
        if any(frames != first for _parent, frames in drawn_from[1:]):
            return None  # same file drawn under different scopes — don't guess
        parent_frames = inherited(drawn_from[0][0], stack | {path}) or []
        out = [*parent_frames, *first]
        resolved[path] = out
        return out

    for child in list(sites):
        inherited(child, frozenset())
    return {path: frames for path, frames in resolved.items() if frames}
