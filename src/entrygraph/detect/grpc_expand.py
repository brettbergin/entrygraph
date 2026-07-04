"""gRPC per-method entrypoint expansion via the binding table (#98 P2 / #37).

A `pb.RegisterFooServer(grpcServer, t.Ingester)` registration exposes every RPC
method of the implementation's type as an entrypoint. The service-level rule
(`go.grpc.service`) can't see the impl's concrete type, so it emits one coarse
marker per service. With the binding table we can resolve `t.Ingester` -> its
type -> that type's exported methods and emit a precise entrypoint per method.

Runs as a scanner pass after ``resolve_bindings`` (needs the type maps). Where
expansion succeeds it replaces the service marker with per-method hints; where it
can't resolve, the marker is left as the fallback.
"""

from __future__ import annotations

import re

from entrygraph.extract.ir import EntrypointHint, FileExtraction
from entrygraph.kinds import EntrypointKind, SymbolKind
from entrygraph.resolve.bindings import FileBindingView
from entrygraph.resolve.symbol_table import SymbolTable

_REGISTER = re.compile(r"^Register(?P<service>[A-Za-z0-9]+)Server$")


def expand_grpc(extractions: list[tuple[str, FileExtraction, bool]], table: SymbolTable) -> int:
    """Expand resolvable gRPC service registrations to per-method entrypoints.

    Returns the number of per-method hints added."""
    added = 0
    for _path, x, is_package in extractions:
        if x.language != "go":
            continue
        view = FileBindingView(x, table, is_package)
        for ref in x.references:
            if ref.kind != "call":
                continue
            m = _REGISTER.match(ref.callee_name)
            if m is None:
                continue
            service = m.group("service")
            methods: list[tuple[str, str]] = []
            if ref.arg_idents:
                impl_type = _resolve_impl_type(
                    ref.arg_idents[-1], ref.caller_qualified_name, view, table
                )
                if impl_type is not None:
                    methods = _exported_methods(impl_type, table)
            if methods:
                _replace_service_marker(x, service)
                for method_name, method_qname in methods:
                    x.entrypoint_hints.append(
                        EntrypointHint(
                            rule_id="go.grpc.method",
                            kind=EntrypointKind.RPC_HANDLER,
                            handler_qualified_name=method_qname,
                            route=f"/{service}/{method_name}",
                            name=f"{service}.{method_name}",
                            framework="grpc-go",
                            span=ref.span,
                            metadata={"impl_type": impl_type},
                        )
                    )
                    added += 1
            elif not _has_service_marker(x, service):
                # couldn't resolve the impl's type — keep the coarse service-level
                # marker as the fallback (matches the go.grpc.service rule, but
                # independent of grpc framework detection)
                x.entrypoint_hints.append(
                    EntrypointHint(
                        rule_id="go.grpc.service",
                        kind=EntrypointKind.RPC_HANDLER,
                        handler_qualified_name=None,
                        route=f"/{service}",
                        name=service,
                        framework="grpc-go",
                        span=ref.span,
                    )
                )
    return added


def _has_service_marker(x: FileExtraction, service: str) -> bool:
    return any(h.rule_id == "go.grpc.service" and h.name == service for h in x.entrypoint_hints)


def _resolve_impl_type(
    impl_arg: str, caller_fqn: str | None, view: FileBindingView, table: SymbolTable
) -> str | None:
    """Resolve the registration's impl argument (`t.Ingester` / `impl`) to a type
    qname via the binding table."""
    if "." in impl_arg:
        receiver, field = impl_arg.split(".", 1)
        recv_type = view.receiver_type(receiver, caller_fqn)
        if recv_type is None:
            return None
        # field of the receiver's type: look up "{RecvType}.{field}"
        field_type = table.field_types.get(f"{recv_type}.{field}")
        return _strip_external_prefix(field_type)
    # a bare local var bound to a constructed impl
    local = view.type_of(impl_arg, caller_fqn)
    return _strip_external_prefix(local)


def _strip_external_prefix(qname: str | None) -> str | None:
    """A project type is a bare FQN; drop the `go:` prefix only if the underlying
    name is actually a project symbol (external types have no methods to walk)."""
    if qname is None:
        return None
    return qname


def _exported_methods(impl_type: str, table: SymbolTable) -> list[tuple[str, str]]:
    """(method name, method fqn) for the exported methods of a project type."""
    candidates = [impl_type]
    if impl_type.startswith("go:"):
        candidates.append(impl_type[3:])
    for cand in candidates:
        child_ids = table.children_by_qname.get(cand)
        if not child_ids:
            continue
        out: list[tuple[str, str]] = []
        for sid in child_ids:
            if table.kinds.get(sid) is not SymbolKind.METHOD:
                continue
            qname = table.qname_of.get(sid, "")
            name = qname.rsplit(".", 1)[-1]
            if name and name[0].isupper():  # exported (Go RPC methods are exported)
                out.append((name, qname))
        if out:
            return out
    return []


def _replace_service_marker(x: FileExtraction, service: str) -> None:
    """Drop the coarse service-level hint for ``service`` once expanded."""
    x.entrypoint_hints = [
        h for h in x.entrypoint_hints if not (h.rule_id == "go.grpc.service" and h.name == service)
    ]
