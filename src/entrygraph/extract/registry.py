"""Language id -> extractor instance."""

from __future__ import annotations

from functools import cache

from entrygraph.extract.base import LanguageExtractor


# (module, class) per language extractor. Imported defensively so a language
# whose module isn't present yet is simply unavailable rather than fatal.
_EXTRACTOR_SPECS = [
    ("entrygraph.extract.python", "PythonExtractor"),
    ("entrygraph.extract.javascript", "JavaScriptExtractor"),
    ("entrygraph.extract.golang", "GoExtractor"),
    ("entrygraph.extract.java", "JavaExtractor"),
    ("entrygraph.extract.ruby", "RubyExtractor"),
]


@cache
def _extractors() -> dict[str, LanguageExtractor]:
    import importlib

    registry: dict[str, LanguageExtractor] = {}
    for module_name, class_name in _EXTRACTOR_SPECS:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue  # extractor not implemented yet
            raise  # a real missing dependency inside the module
        extractor = getattr(module, class_name)()
        for lang_id in extractor.language_ids:
            registry[lang_id] = extractor
    return registry


def extractor_for(lang_id: str) -> LanguageExtractor | None:
    return _extractors().get(lang_id)
