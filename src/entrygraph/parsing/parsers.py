"""Parser construction over tree-sitter-language-pack.

We build tree_sitter.Parser objects from the pack's Language handles rather
than using tree_sitter_language_pack.get_parser(), whose bundled Parser class
is incompatible with the py-tree-sitter 0.25 API surface we use.

Parsers and Language handles are cached per process; they are created lazily
inside worker processes and must never be pickled.
"""

from __future__ import annotations

from functools import cache

from tree_sitter import Language, Parser

# our language ids -> tree-sitter-language-pack grammar names
_GRAMMAR_NAMES = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "java": "java",
    "ruby": "ruby",
    "csharp": "csharp",
    "php": "php",
    "rust": "rust",
}


def supported(lang_id: str) -> bool:
    return lang_id in _GRAMMAR_NAMES


@cache
def language(lang_id: str) -> Language:
    from tree_sitter_language_pack import get_language

    return get_language(_GRAMMAR_NAMES[lang_id])


@cache
def parser(lang_id: str) -> Parser:
    return Parser(language(lang_id))


def parse(lang_id: str, source: bytes):
    return parser(lang_id).parse(source)
