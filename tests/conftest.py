# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_readonly_bits(tmp_path: Path):
    """Sealed logs are mode 0444. Pytest must be able to delete the tmp tree."""
    yield
    if not tmp_path.exists():
        return
    for path in tmp_path.rglob("*"):
        try:
            mode = stat.S_IWRITE | stat.S_IREAD
            if path.is_dir():
                mode |= stat.S_IEXEC
            os.chmod(path, mode)
        except OSError:
            pass


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: in-process tests")
    config.addinivalue_line("markers", "acceptance: end-to-end drill tests")
