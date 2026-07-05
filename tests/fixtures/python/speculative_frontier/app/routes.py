"""Two SQL "paths" to compare (#136):

- ``direct`` is a clean single-guess lead: the handler reads a request value and
  calls an unresolved wildcard sink (`cursor.execute`) directly. One speculative
  hop.
- ``stitched`` is the Laravel-style cross-component chain: a fuzzy method-dispatch
  (`builder.extend`, a unique-name bind to an unrelated component) bridges into a
  wildcard sink. A fuzzy interior hop *plus* the unresolved sink — two speculative
  hops — so it must rank below the clean lead.
"""

from flask import request

from .scope import SoftDeletingScope


def direct():
    q = request.args.get("q")
    cursor = _cursor()
    return cursor.execute("select * from t where n = " + q)


def stitched():
    q = request.args.get("q")
    scope = SoftDeletingScope()
    return scope.apply(q)


def _cursor():
    return object()
