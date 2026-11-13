# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import time

import pytest

from tests.support import start_live

pytestmark = pytest.mark.acceptance


def test_at_02_call_after_ttl_is_expired(tmp_path) -> None:
    live = start_live(tmp_path, token_ttl_s=2, tool_ttl_s=2)
    try:
        issued = live.issue("files", "read")
        assert issued["status"] == 200, issued
        time.sleep(3)
        call = live.call("files", "read", issued["body"]["token"], {"path": "INV-001.json"}, "at02")
        assert call["status"] == 401
        assert call["body"]["detail"] == "TOKEN_EXPIRED"
    finally:
        live.close()
