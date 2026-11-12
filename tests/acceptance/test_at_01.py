# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

pytestmark = pytest.mark.acceptance


def test_at_01_payments_token_cannot_send_email(live) -> None:
    issued = live.issue("payments", "initiate")
    assert issued["status"] == 200
    call = live.call(
        "email",
        "send",
        issued["body"]["token"],
        {"to": "a@b.example", "subject": "x", "body": "x", "message_id": "at01"},
        "at01",
    )
    assert call["status"] == 403
    assert call["body"]["detail"] == "SCOPE_MISMATCH"
