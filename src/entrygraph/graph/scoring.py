"""Query-time path risk scoring (heuristic taint tier).

Everything here runs at query time, never at index time, so weights can be
retuned without re-indexing. A path's risk combines: the terminal sink's
severity, the weakest edge confidence along the path, path length, whether any
hop is speculative (CHA/dynamic/callback), whether a sanitizer intervened,
whether the sink was called with only constant arguments, and whether the
source's user-controlled parameters are known.
"""

from __future__ import annotations

import re

from entrygraph.kinds import Confidence

_SEVERITY_BASE = {"critical": 1.0, "high": 0.85, "medium": 0.6, "low": 0.35}
_DEFAULT_SEVERITY = 0.6

_CONFIDENCE_WEIGHT = {
    int(Confidence.EXACT): 1.0,
    int(Confidence.IMPORT): 0.95,
    int(Confidence.FUZZY): 0.7,
    int(Confidence.UNRESOLVED): 0.5,
}

_SPECULATIVE_VIA = {"cha", "dynamic"}
_LENGTH_DECAY = 0.97

# Channel nudge (#87): body/query/path/form are attacker-controlled on any
# request; headers and cookies are often proxy- or server-set, so a finding
# sourced only from them is slightly lower-signal.
_CHANNEL_WEIGHT = {"header": 0.85, "cookie": 0.85}

# Input names that conventionally feed dangerous sinks — a known key from this
# set nudges risk up (capped at 1.0 overall).
_RISKY_KEYS = frozenset(
    {
        "id",
        "path",
        "file",
        "filename",
        "url",
        "uri",
        "cmd",
        "command",
        "dir",
        "q",
        "query",
        "target",
    }
)

# A literal-only argument preview: strings, numbers, bools, None, kwarg names,
# and bracketed literal collections. Anything with an identifier/operator that
# could carry a variable makes it non-constant.
_CONST_TOKEN = re.compile(
    r"""^(
        \s | , | = | \( | \) | \[ | \] | \{ | \} | : |
        '[^']*' | "[^"]*" | `(?:[^`$]|\$(?!\{))*` |   # backtick: no ${...} interpolation
        \d[\d_.eExXaAbBcCdDfF]* |
        True|False|None|null|true|false|nil |
        [A-Za-z_]\w*\s*=          # kwarg name before '='
    )*$""",
    re.VERBOSE,
)


def is_constant_args(arg_preview: str | None) -> bool:
    """True if the sink was called with only literal/constant arguments.

    Conservative: an empty/None preview is constant (no args); a preview that was
    truncated at the 80-char cap (trailing ellipsis) returns False because we
    can't see the whole argument list.
    """
    if not arg_preview:
        return True
    text = arg_preview.strip()
    if text.endswith("…") or text.endswith("..."):
        return False
    inner = text
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    if not inner.strip():
        return True
    return bool(_CONST_TOKEN.match(text))


def confidence_factor(confidences: list[int]) -> float:
    return min((_CONFIDENCE_WEIGHT.get(c, 0.5) for c in confidences), default=1.0)


def score_path(
    *,
    hop_confidences: list[int],
    hop_vias: list[str | None],
    sink_severity: str | None,
    sanitized_effect: str | None,  # "neutralizes" | "reduces" | None
    constant_args: bool,
    source_tainted: bool,
    source_channel: str | None = None,
    source_key: str | None = None,
) -> float:
    """Return a risk score in [0, 1]; higher = riskier. See module docstring."""
    severity_base = _SEVERITY_BASE.get(sink_severity or "", _DEFAULT_SEVERITY)
    conf = confidence_factor(hop_confidences)
    hops = max(len(hop_confidences), 1)
    length_decay = _LENGTH_DECAY ** (hops - 1)
    speculative = 0.85 if any(v in _SPECULATIVE_VIA for v in hop_vias) else 1.0
    if sanitized_effect == "neutralizes":
        sanitizer_factor = 0.0
    elif sanitized_effect == "reduces":
        sanitizer_factor = 0.3
    else:
        sanitizer_factor = 1.0
    const_factor = 0.4 if constant_args else 1.0
    source_factor = 1.0 if source_tainted else 0.9
    channel_factor = _CHANNEL_WEIGHT.get(source_channel or "", 1.0)
    key_factor = 1.05 if source_key and source_key.lower() in _RISKY_KEYS else 1.0
    risk = (
        severity_base
        * conf
        * length_decay
        * speculative
        * sanitizer_factor
        * const_factor
        * source_factor
        * channel_factor
        * key_factor
    )
    return round(min(risk, 1.0), 4)
