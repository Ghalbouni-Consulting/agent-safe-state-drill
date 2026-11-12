# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_15_b7_harness_comes_back_latched(completed_drill) -> None:
    row = _bypass(completed_drill, "B7")
    assert row["pass"] is True
    assert "SAFE_STATE" in row["observed"]
