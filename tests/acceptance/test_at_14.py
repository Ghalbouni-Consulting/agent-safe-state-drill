# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_14_b6_agent_restart_is_refused(completed_drill) -> None:
    row = _bypass(completed_drill, "B6")
    assert row["pass"] is True
    assert "launcher 3" in row["observed"]
