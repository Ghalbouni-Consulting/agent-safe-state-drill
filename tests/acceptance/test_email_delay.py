# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import time

import pytest

from tests.support import start_live

pytestmark = pytest.mark.acceptance


def test_running_harness_honours_a_shorter_email_delay(tmp_path) -> None:
    live = start_live(tmp_path, email_dispatch_delay_ms=30000)
    try:
        updated = live.admin.post("/admin/outbound/email-delay", json={"delay_ms": 150})
        assert updated.status_code == 200
        assert updated.json()["email_dispatch_delay_ms"] == 150
        issued = live.issue("email", "send")
        assert issued["status"] == 200, issued
        call = live.call(
            "email",
            "send",
            issued["body"]["token"],
            {
                "to": "ap@northwind.example",
                "subject": "delay",
                "body": "still queued",
                "message_id": "msg-delay",
            },
            "email-delay",
        )
        assert call["status"] == 202, call
        outbox = live.run / "sinks" / "outbox.jsonl"
        assert not outbox.exists() or "msg-delay" not in outbox.read_text(encoding="utf-8")
        time.sleep(0.7)
        assert "msg-delay" in outbox.read_text(encoding="utf-8")
    finally:
        live.close()
