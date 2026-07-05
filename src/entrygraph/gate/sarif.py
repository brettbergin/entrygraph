"""SARIF 2.1.0 serialization of gate findings for GitHub code scanning (#116).

Each new/known path becomes one SARIF result whose ``partialFingerprints`` carry
our stable path fingerprints, so GitHub tracks a finding across commits instead
of re-alerting on every push. The sink call site is the primary location; the
full source -> sink chain is attached as a ``codeFlow`` for reviewers.
"""

from __future__ import annotations

from entrygraph.gate.store import GateFinding

_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_INFO_URI = "https://github.com/brettbergin/entrygraph"


def _primary_location(finding: GateFinding) -> dict | None:
    """The sink call site: the last hop that has a real (in-repo) file."""
    for hop in reversed(finding.hops):
        if hop.get("file"):
            return {
                "physicalLocation": {
                    "artifactLocation": {"uri": hop["file"]},
                    "region": {"startLine": max(1, int(hop.get("line") or 1))},
                }
            }
    return None


def _code_flow(finding: GateFinding) -> dict | None:
    locations = [
        {
            "location": {
                "physicalLocation": {
                    "artifactLocation": {"uri": hop["file"]},
                    "region": {"startLine": max(1, int(hop.get("line") or 1))},
                },
                "message": {"text": hop.get("qname", "")},
            }
        }
        for hop in finding.hops
        if hop.get("file")
    ]
    if not locations:
        return None
    return {"threadFlows": [{"locations": locations}]}


def _rules(findings: list[GateFinding]) -> list[dict]:
    categories = sorted({f.sink_category for f in findings if f.sink_category})
    return [
        {
            "id": category,
            "name": category,
            "shortDescription": {"text": f"Reachable {category} sink"},
            "defaultConfiguration": {"level": "warning"},
        }
        for category in categories
    ]


def _result(finding: GateFinding, *, threshold: float) -> dict:
    source = finding.hops[0]["qname"] if finding.hops else "source"
    sink = finding.sink_id or (finding.hops[-1]["qname"] if finding.hops else "sink")
    result: dict = {
        "ruleId": finding.sink_category or "reachability",
        "level": "error" if finding.risk >= threshold else "warning",
        "message": {
            "text": (
                f"Reachable {finding.sink_category or 'dangerous'} path: "
                f"{source} -> {sink} (risk {finding.risk:.2f})"
            )
        },
        "partialFingerprints": {
            "entrygraph/strict": finding.strict,
            "entrygraph/endpoint": finding.endpoint,
        },
    }
    location = _primary_location(finding)
    if location is not None:
        result["locations"] = [location]
    flow = _code_flow(finding)
    if flow is not None:
        result["codeFlows"] = [flow]
    return result


def to_sarif(
    findings: list[GateFinding], *, threshold: float = 0.5, tool_version: str = "0.0.0"
) -> dict:
    """A SARIF 2.1.0 log for ``findings`` (typically the gate's new + known sets)."""
    return {
        "version": "2.1.0",
        "$schema": _SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "entrygraph",
                        "informationUri": _INFO_URI,
                        "version": tool_version,
                        "rules": _rules(findings),
                    }
                },
                "results": [_result(f, threshold=threshold) for f in findings],
            }
        ],
    }
