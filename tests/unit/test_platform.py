# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Cross-platform checks that the CI matrix runs on Linux, macOS and Windows."""

from __future__ import annotations

from pathlib import Path

import pytest

from ksdrill.clock import paris_iso
from ksdrill.util import clear_readonly, is_readonly, set_readonly

pytestmark = pytest.mark.unit


def test_readonly_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sealed.txt"
    path.write_text("ok", encoding="utf-8")
    set_readonly(path)
    assert is_readonly(path)
    clear_readonly(path)
    path.write_text("rewritten", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "rewritten"


def test_paris_timestamp_has_an_offset() -> None:
    stamp = paris_iso()
    assert "+" in stamp or stamp.endswith("Z")
    assert "T" in stamp
