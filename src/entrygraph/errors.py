"""Exception hierarchy for entrygraph."""

from __future__ import annotations


class EntrygraphError(Exception):
    """Base class for all entrygraph errors."""


class SchemaMismatchError(EntrygraphError):
    """The database was written by an incompatible entrygraph schema version."""


class DatabaseNotFoundError(EntrygraphError):
    """No index database exists at the given path."""


class SymbolNotFoundError(EntrygraphError):
    """No symbol matches the given qualified name."""


class RepositoryNotIndexedError(EntrygraphError):
    """The database contains no indexed repository."""


class UnsupportedLanguageError(EntrygraphError):
    """No extractor is registered for the requested language."""
