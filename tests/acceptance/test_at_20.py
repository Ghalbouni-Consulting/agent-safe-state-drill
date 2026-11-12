# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from ksdrill.config import load_clause_map
from ksdrill.evidence.card import DISCLAIMER, build_card, render_html
from ksdrill.evidence.pdf import page_count
from tests.unit.test_config_and_clauses import _synthetic_model

pytestmark = pytest.mark.acceptance


def test_at_20_evidence_card_is_one_page_and_clause_mapped(completed_drill) -> None:
    html_path = build_card(completed_drill.run_dir, write_pdf_file=True)
    html = html_path.read_text(encoding="utf-8")
    pdf = completed_drill.run_dir / "evidence" / "card.pdf"
    assert pdf.exists()
    assert page_count(pdf) == 1
    assert DISCLAIMER in html
    assert "demo budget" in html
    assert "our target" in html
    clause_file = load_clause_map(completed_drill.run_dir / "config" / "clause_map.yaml")
    ids = {clause.id for clause in clause_file.clauses}
    for step in completed_drill.result["steps"]:
        assert step["clauses"]
        assert set(step["clauses"]) <= ids
    for row in completed_drill.result["bypasses"]:
        assert row["clauses"]
        assert set(row["clauses"]) <= ids
    assert "EU-14" in html and "EU-15" in html
    # The shipped map has no unverified rows. The label is still implemented.
    synthetic = render_html(_synthetic_model(), 8)
    assert "UNVERIFIED" in synthetic
    assert 'class="unverified"' in synthetic or "unverified" in synthetic
    assert completed_drill.result["verdict"] == "PASS"
