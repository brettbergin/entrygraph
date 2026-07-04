"""Pure-unit reaching-defs facts + verdict tests (#96 Phase 2), no DB."""

from __future__ import annotations

import pytest

from entrygraph.analysis.facts import extract_function_facts, language_supported
from entrygraph.analysis.reaching import reaches


def _py(body: str):
    # the function spans the whole source; locating it by its def line ([1,1]) is
    # enough — extract collects every fact in the body regardless of the range
    src = ("def handler(req):\n" + body).encode()
    return extract_function_facts("python", src, 1, 1)


def test_direct_assignment_flow():
    f = _py("    q = request.args.get('q')\n    os.system(q)\n")
    assert reaches(f, set(), {2}, 3, "system") is True


def test_concatenation_flow():
    f = _py("    q = request.args.get('q')\n    os.system('echo ' + q)\n")
    assert reaches(f, set(), {2}, 3, "system") is True


def test_fstring_flow():
    f = _py("    q = request.args.get('q')\n    os.system(f'echo {q}')\n")
    assert reaches(f, set(), {2}, 3, "system") is True


def test_augmented_assign_flow():
    f = _py("    q = request.args.get('q')\n    cmd = 'echo '\n    cmd += q\n    os.system(cmd)\n")
    assert reaches(f, set(), {2}, 5, "system") is True


def test_taint_through_unknown_helper_stays_tainted():
    f = _py("    q = request.args.get('q')\n    y = sanitize(q)\n    os.system(y)\n")
    # conservative: a value passing through any helper keeps taint (no summaries yet)
    assert reaches(f, set(), {2}, 4, "system") is True


def test_loop_reassignment_fixpoint():
    f = _py(
        "    q = request.args.get('q')\n"
        "    for i in range(3):\n"
        "        cmd = q\n"
        "        q = cmd\n"
        "    os.system(cmd)\n"
    )
    assert reaches(f, set(), {2}, 6, "system") is True


def test_inline_accessor_in_sink_arg():
    f = _py("    os.system(request.args.get('q'))\n")
    assert reaches(f, set(), {2}, 2, "system") is True


def test_unrelated_local_refuted():
    f = _py("    q = request.args.get('q')\n    x = 'ls'\n    os.system(x)\n")
    assert reaches(f, set(), {2}, 4, "system") is False


def test_constant_sink_refuted():
    f = _py("    q = request.args.get('q')\n    os.system('ls -l')\n")
    assert reaches(f, set(), {2}, 3, "system") is False


def test_sink_not_found_is_unknown():
    f = _py("    q = request.args.get('q')\n    os.system(q)\n")
    assert reaches(f, set(), {2}, 99, "system") is None


def test_handler_param_seed():
    f = _py("    os.system(req)\n")
    assert reaches(f, set(f.params), set(), 2, "system") is True


def test_js_member_read_flow():
    src = b"function h(req, res) {\n  const n = req.body.name;\n  exec(n);\n}\n"
    f = extract_function_facts("javascript", src, 1, 1)
    assert reaches(f, set(f.params), set(), 3, "exec") is True


def test_js_unrelated_refuted():
    src = b"function h(req, res) {\n  const n = req.body.name;\n  exec('ls');\n}\n"
    f = extract_function_facts("javascript", src, 1, 1)
    assert reaches(f, set(f.params), set(), 3, "exec") is False


def test_unsupported_language_returns_none():
    assert not language_supported("go")
    assert extract_function_facts("go", b"func h() {}", 1, 1) is None


def test_module_level_source_has_no_function():
    # no enclosing function covering the range -> None (not an error)
    assert extract_function_facts("python", b"x = 1\n", 1, 1) is None


@pytest.mark.parametrize("lang", ["python", "javascript", "typescript", "tsx"])
def test_supported_languages(lang):
    assert language_supported(lang)
