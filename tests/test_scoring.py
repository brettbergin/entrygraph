from __future__ import annotations

from entrygraph.graph.scoring import is_constant_args, score_path
from entrygraph.kinds import Confidence


def test_is_constant_args():
    assert is_constant_args(None)
    assert is_constant_args("()")
    assert is_constant_args("('ls -la', 42, True)")
    assert is_constant_args("(timeout=30, shell=False)")
    # a variable in the args -> not constant
    assert not is_constant_args("(user_input,)")
    assert not is_constant_args("(cmd + ' arg')")
    # truncated preview -> can't be sure -> not constant
    assert not is_constant_args("('very long literal that got cut o…")
    assert not is_constant_args("(a, b, c, d, e, f, ...")


def test_score_path_ordering_by_severity():
    common = {
        "hop_confidences": [int(Confidence.EXACT)],
        "hop_vias": [None],
        "sanitized_effect": None,
        "constant_args": False,
        "source_tainted": True,
    }
    critical = score_path(sink_severity="critical", **common)
    low = score_path(sink_severity="low", **common)
    assert critical > low


def test_score_path_confidence_and_length_penalty():
    high_conf = score_path(
        hop_confidences=[int(Confidence.EXACT)],
        hop_vias=[None],
        sink_severity="high",
        sanitized_effect=None,
        constant_args=False,
        source_tainted=True,
    )
    low_conf = score_path(
        hop_confidences=[int(Confidence.FUZZY)],
        hop_vias=[None],
        sink_severity="high",
        sanitized_effect=None,
        constant_args=False,
        source_tainted=True,
    )
    assert high_conf > low_conf
    long_path = score_path(
        hop_confidences=[int(Confidence.EXACT)] * 6,
        hop_vias=[None] * 6,
        sink_severity="high",
        sanitized_effect=None,
        constant_args=False,
        source_tainted=True,
    )
    assert long_path < high_conf  # length decay


def test_score_path_sanitizer_and_constant_args():
    base = {
        "hop_confidences": [int(Confidence.EXACT)],
        "hop_vias": [None],
        "sink_severity": "critical",
        "source_tainted": True,
    }
    neutralized = score_path(sanitized_effect="neutralizes", constant_args=False, **base)
    reduced = score_path(sanitized_effect="reduces", constant_args=False, **base)
    clean = score_path(sanitized_effect=None, constant_args=False, **base)
    const = score_path(sanitized_effect=None, constant_args=True, **base)
    assert neutralized == 0.0
    assert reduced < clean
    assert const < clean  # constant args discount


def test_score_path_speculative_via_discount():
    plain = score_path(
        hop_confidences=[int(Confidence.FUZZY)],
        hop_vias=[None],
        sink_severity="high",
        sanitized_effect=None,
        constant_args=False,
        source_tainted=True,
    )
    cha = score_path(
        hop_confidences=[int(Confidence.FUZZY)],
        hop_vias=["cha"],
        sink_severity="high",
        sanitized_effect=None,
        constant_args=False,
        source_tainted=True,
    )
    assert cha < plain
