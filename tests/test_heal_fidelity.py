"""Incremental healing must not alter an edge's confidence tier (Phase 2.4).

Regression: `_heal_dangling_edges` re-bound every healed edge at Confidence.IMPORT
unconditionally. A cross-file FUZZY (or via="cha") edge whose target file was
merely touched came back as IMPORT, so a min_confidence>=IMPORT query returned
edges a full re-index would score FUZZY.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from entrygraph.db.engine import make_engine
from entrygraph.db.models import Edge, Symbol
from entrygraph.pipeline.scanner import index_repository

HEAL_APP = Path(__file__).parent / "fixtures" / "python" / "heal_fidelity"


@pytest.fixture
def repo(tmp_path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(HEAL_APP, dst)
    return dst


def _edge_confidence(engine, src_qname, dst_qname) -> int | None:
    with Session(engine) as s:
        ids = {sym.qname: sym.id for sym in s.execute(select(Symbol)).scalars()}
        src = ids.get(src_qname)
        row = s.execute(
            select(Edge.confidence).where(Edge.src_symbol_id == src, Edge.dst_qname == dst_qname)
        ).first()
    return row[0] if row else None


def test_healed_fuzzy_edge_keeps_its_confidence(repo, tmp_path):
    from entrygraph.kinds import Confidence

    engine = make_engine(tmp_path / "inc.db")
    index_repository(repo, engine)
    before = _edge_confidence(engine, "caller.go", "worker.Worker.process")
    assert before == int(Confidence.FUZZY)  # cross-file unique-name fuzzy edge

    # touch worker.py: Worker.process gets a new id, the inbound edge is SET NULL
    # then healed. Its tier must stay FUZZY, not be upgraded to IMPORT.
    worker = repo / "worker.py"
    worker.write_text(worker.read_text() + "\n# touch\n")
    os.utime(worker, ns=(0, 10**18))
    index_repository(repo, engine, incremental=True)

    after = _edge_confidence(engine, "caller.go", "worker.Worker.process")
    assert after == int(Confidence.FUZZY)

    # and it matches a full re-index
    full = make_engine(tmp_path / "full.db")
    index_repository(repo, full)
    assert after == _edge_confidence(full, "caller.go", "worker.Worker.process")
    full.dispose()
    engine.dispose()
