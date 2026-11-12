# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

pytestmark = pytest.mark.acceptance


def test_at_08_b1_old_token_is_rejected(completed_drill) -> None:
    row = _bypass(completed_drill, "B1")
    assert row["pass"] is True
    assert "409" in row["observed"] and "HALTED" in row["observed"]
    assert "401" in row["observed"] and "TOKEN_REVOKED" in row["observed"]


def _bypass(outcome, bid: str) -> dict:
    for row in outcome.result["bypasses"]:
        if row["id"] == bid:
            return row
    raise AssertionError(bid)
