# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_11_b4_tool_calls_are_refused(completed_drill) -> None:
    row = _bypass(completed_drill, "B4")
    assert row["pass"] is True
    assert "shell.exec" in row["observed"]
