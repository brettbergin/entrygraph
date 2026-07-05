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


def test_aws_lambda_js_detected_from_serverless_config():
    # the javascript.aws-lambda.handler entrypoint rule is gated on this spec;
    # regression: the spec was missing, so the rule could never fire.
    detected = detect_frameworks(
        ManifestDeps(javascript={"@types/aws-lambda"}),
        import_signals=set(),
        file_paths=["serverless.yml"],
        languages_present={"javascript"},
    )
    assert any(d.name == "aws-lambda-js" for d in detected)


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


# ---------------- C6: additional framework specs ----------------


def test_c6_new_frameworks_detected():
    cases = [
        ("python", {"tornado"}, ("python", "tornado"), "tornado"),
        ("javascript", {"koa"}, ("javascript", "koa"), "koa"),
        ("go", {"github.com/go-chi/chi"}, ("go", "github.com/go-chi/chi"), "chi"),
        ("java", {"io.micronaut.micronaut-core"}, ("java", "io.micronaut.http"), "micronaut"),
        ("ruby", {"sidekiq"}, ("ruby", "sidekiq"), "sidekiq"),
    ]
    for lang, deps, import_sig, name in cases:
        manifests = ManifestDeps(**{lang if lang != "javascript" else "javascript": deps})
        detected = {
            d.name
            for d in detect_frameworks(
                manifests, import_signals={import_sig}, file_paths=[], languages_present={lang}
            )
        }
        assert name in detected, f"{name} not detected"


def test_c6_file_presence_frameworks():
    detected = {
        d.name
        for d in detect_frameworks(
            ManifestDeps(),
            import_signals=set(),
            file_paths=["config.ru", "app/routes/index.tsx"],
            languages_present={"ruby", "javascript"},
        )
    }
    assert "rack" in detected


def test_rails_detected_without_manifest_dep():
    # the rails monorepo (and apps depending on a component subset) has no top-level
    # `rails` gem dep; detect it from code signals instead (#116 QA regression)
    detected = detect_frameworks(
        ManifestDeps(),  # no manifest deps at all
        import_signals={("ruby", "action_controller"), ("ruby", "active_record")},
        file_paths=["config/routes.rb", "app/controllers/users_controller.rb"],
        languages_present={"ruby"},
        symbol_names={"ApplicationController", "UsersController"},
    )
    rails = next((d for d in detected if d.name == "rails"), None)
    assert rails is not None and rails.confidence > 0.8
