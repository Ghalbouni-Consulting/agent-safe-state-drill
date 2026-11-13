# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_12_b5_outbound_stays_paused(completed_drill) -> None:
    row = _bypass(completed_drill, "B5")
    assert row["pass"] is True
    assert "401" in row["observed"] and "ADMIN_AUTH" in row["observed"]
