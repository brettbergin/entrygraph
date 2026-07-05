"""Sentinel: the self-hostable GitHub App + service around the reachability gate.

The zero-infrastructure half of the Continuous Reachability Gate (#116) ships as
the ``entrygraph gate`` CLI + GitHub Action. Sentinel (#126) is the optional
networked layer: a GitHub App whose webhook receiver runs the same gate on pull
requests, maintains per-repo baselines centrally, and posts a Check Run.

It reuses the gate engine unchanged — entrygraph never executes analyzed code, so
scanning an untrusted PR is a parse-and-query operation, not a sandbox problem.
Everything here lives behind the ``entrygraph[sentinel]`` extra so the core CLI
stays dependency-lean.
"""

from __future__ import annotations
