# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_09_b2_forged_and_tampered_tokens(completed_drill) -> None:
    row = _bypass(completed_drill, "B2")
    assert row["pass"] is True
    assert row["observed"].count("TOKEN_INVALID") >= 2
