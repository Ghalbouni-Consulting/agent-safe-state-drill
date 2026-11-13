# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.support import run_scenario, tool_call_digest

pytestmark = pytest.mark.acceptance


def test_at_06_two_mock_runs_have_the_same_tool_sequence(tmp_path) -> None:
    first = run_scenario(tmp_path / "a")
    second = run_scenario(tmp_path / "b")
    try:
        assert tool_call_digest(first.run) == tool_call_digest(second.run)
        assert len(tool_call_digest(first.run)) == 9
    finally:
        first.close()
        second.close()
