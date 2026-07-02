from __future__ import annotations

from entrygraph.detect.frameworks import detect_frameworks
from entrygraph.detect.manifests import ManifestDeps


def test_manifest_plus_import_boosts_confidence():
    manifests = ManifestDeps(python={"flask", "celery"})
    detected = detect_frameworks(
        manifests,
        import_signals={("python", "flask")},
        file_paths=[],
        languages_present={"python"},
    )
    by_name = {d.name: d for d in detected}
    assert by_name["flask"].confidence > 0.9  # dep + import
    assert 0.7 <= by_name["celery"].confidence < 0.9  # dep only


def test_import_only_framework():
    detected = detect_frameworks(
        ManifestDeps(),
        import_signals={("python", "argparse")},
        file_paths=[],
        languages_present={"python"},
    )
    assert any(d.name == "argparse" for d in detected)


def test_language_gating():
    manifests = ManifestDeps(ruby={"rails"})
    detected = detect_frameworks(manifests, set(), [], languages_present={"python"})
    assert not any(d.name == "rails" for d in detected)


def test_typescript_counts_for_js_frameworks():
    manifests = ManifestDeps(javascript={"express"})
    detected = detect_frameworks(
        manifests,
        import_signals={("typescript", "express")},
        file_paths=[],
        languages_present={"javascript", "typescript"},
    )
    express = next(d for d in detected if d.name == "express")
    assert express.confidence > 0.9


def test_file_presence_signal():
    detected = detect_frameworks(
        ManifestDeps(ruby={"rails"}),
        set(),
        ["config/routes.rb", "app/models/user.rb"],
        languages_present={"ruby"},
    )
    rails = next(d for d in detected if d.name == "rails")
    assert any("config/routes.rb" in e for e in rails.evidence)
