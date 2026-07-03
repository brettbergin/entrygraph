from __future__ import annotations

from pathlib import Path

from entrygraph.detect.manifests import (
    parse_build_gradle,
    parse_gemfile,
    parse_go_mod,
    parse_manifests,
    parse_package_json,
    parse_pom_xml,
    parse_pyproject_toml,
    parse_requirements_txt,
)


def test_requirements_txt():
    deps = parse_requirements_txt(
        "flask>=3.0\nClick==8.1\n# comment\n-r other.txt\nDjango_Extensions[extra]\n"
    )
    assert deps == {"flask", "click", "django-extensions"}


def test_pyproject_toml():
    deps = parse_pyproject_toml(
        '[project]\ndependencies = ["fastapi>=0.100", "uvicorn[standard]"]\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\n'
        '[tool.poetry.dependencies]\npython = "^3.11"\ncelery = "*"\n'
    )
    assert deps == {"fastapi", "uvicorn", "pytest", "celery"}


def test_package_json():
    deps = parse_package_json(
        '{"dependencies": {"express": "^4"}, "devDependencies": {"@nestjs/core": "10"}}'
    )
    assert deps == {"express", "@nestjs/core"}
    assert parse_package_json("not json") == set()


def test_go_mod():
    deps = parse_go_mod(
        "module example.com/app\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n"
        "\tgolang.org/x/sync v0.5.0\n)\nrequire github.com/spf13/cobra v1.8.0\n"
    )
    assert "github.com/gin-gonic/gin" in deps
    assert "github.com/spf13/cobra" in deps


def test_go_mod_excludes_indirect_deps():
    # `// indirect` requires are transitive, not direct usage — they produced
    # spurious framework detections (gitea gorilla-mux, grpc-go) (#38 / F-H18).
    deps = parse_go_mod(
        "module example.com/app\n\nrequire (\n"
        "\tgithub.com/gin-gonic/gin v1.9.1\n"
        "\tgithub.com/gorilla/mux v1.8.1 // indirect\n"
        ")\n"
        "require github.com/spf13/cobra v1.8.0 // indirect\n"
    )
    assert deps == {"github.com/gin-gonic/gin"}  # both indirect requires dropped


def test_pom_xml():
    deps = parse_pom_xml(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies>'
        "<dependency><groupId>org.springframework.boot</groupId>"
        "<artifactId>spring-boot-starter-web</artifactId></dependency>"
        "</dependencies></project>"
    )
    assert deps == {"org.springframework.boot:spring-boot-starter-web"}


def test_build_gradle():
    deps = parse_build_gradle(
        "dependencies {\n"
        "  implementation 'org.springframework.boot:spring-boot-starter-web:3.2.0'\n"
        '  testImplementation("junit:junit:4.13")\n}\n'
    )
    assert "org.springframework.boot:spring-boot-starter-web:3.2.0" in deps


def test_gemfile():
    deps = parse_gemfile('source "https://rubygems.org"\ngem "rails", "~> 7.1"\ngem \'sinatra\'\n')
    assert deps == {"rails", "sinatra"}


def test_parse_manifests_fixture():
    fixtures = Path(__file__).parent / "fixtures" / "python" / "flask_app"
    deps = parse_manifests(fixtures)
    assert {"flask", "click", "requests"} <= deps.python
    assert "requirements.txt" in deps.sources


def test_parse_manifests_reads_js_workspace_packages(tmp_path: Path):
    # `packages/<name>/package.json` is the standard JS workspace layout and must
    # not be excluded (regression: "packages" was in the skip list).
    pkg = tmp_path / "packages" / "api"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"dependencies": {"express": "^4"}}')
    deps = parse_manifests(tmp_path)
    assert "express" in deps.javascript
    assert "packages/api/package.json" in deps.sources


def test_parse_manifests_skips_benchmark_and_example_manifests(tmp_path: Path):
    # Non-app subprojects carry their own deps that aren't the repo's framework —
    # they produced spurious express/react detections (hono/strapi) (#38 / F-H18).
    for sub in ("benchmarks/webapp", "examples/demo", "docs"):
        d = tmp_path / sub
        d.mkdir(parents=True)
        (d / "package.json").write_text('{"dependencies": {"express": "^4", "react": "^18"}}')
    deps = parse_manifests(tmp_path)
    assert "express" not in deps.javascript
    assert "react" not in deps.javascript


def test_parse_manifests_finds_deep_monorepo_manifests(tmp_path: Path):
    # nopcommerce nests .csproj at src/Libraries/<Proj>/<Proj>.csproj (depth 3);
    # a shallow search read 0 C# deps and left detection empty (#38 / F-H30).
    proj = tmp_path / "src" / "Libraries" / "Nop.Core"
    proj.mkdir(parents=True)
    (proj / "Nop.Core.csproj").write_text(
        '<Project><ItemGroup><PackageReference Include="AutoMapper" Version="1" />'
        "</ItemGroup></Project>"
    )
    deps = parse_manifests(tmp_path)
    assert "automapper" in deps.csharp


# ---------------- C1: C#, PHP, Rust manifests ----------------
from entrygraph.detect.manifests import (  # noqa: E402
    parse_cargo_toml,
    parse_composer_json,
    parse_csproj,
    parse_packages_config,
)


def test_parse_csproj():
    deps = parse_csproj(
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <PackageReference Include="Microsoft.AspNetCore.App" Version="8.0.0" />\n'
        '    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )
    assert "microsoft.aspnetcore.app" in deps and "newtonsoft.json" in deps


def test_parse_packages_config():
    deps = parse_packages_config(
        '<packages><package id="EntityFramework" version="6.4.4" /></packages>'
    )
    assert deps == {"entityframework"}


def test_parse_composer_json():
    deps = parse_composer_json(
        '{"require": {"php": ">=8.1", "laravel/framework": "^10.0", "ext-json": "*"},'
        ' "require-dev": {"phpunit/phpunit": "^10.0"}}'
    )
    assert deps == {"laravel/framework", "phpunit/phpunit"}  # php + ext-* dropped


def test_parse_cargo_toml():
    deps = parse_cargo_toml(
        '[dependencies]\naxum = "0.7"\ntokio = { version = "1", features = ["full"] }\n'
        '[dev-dependencies]\nreqwest = "0.11"\n'
        '[build-dependencies]\ncc = "1"\n'
    )
    assert {"axum", "tokio", "reqwest", "cc"} <= deps


def test_new_manifest_parsers_handle_malformed():
    assert parse_csproj("<not xml") == set()
    assert parse_composer_json("{bad json") == set()
    assert parse_cargo_toml("[[[") == set()
