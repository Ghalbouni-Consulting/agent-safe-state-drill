# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json

import pytest

from tests.acceptance.test_at_08 import _bypass

pytestmark = pytest.mark.acceptance


def test_at_13_b9_inflight_email_is_held(completed_drill) -> None:
    row = _bypass(completed_drill, "B9")
    assert row["pass"] is True
    data = json.loads((completed_drill.run_dir / "state" / "outbound.json").read_text(encoding="utf-8"))
    held = [item for item in data["items"] if item["channel"] == "email" and item["status"] == "HELD"]
    assert held
    outbox = completed_drill.run_dir / "sinks" / "outbox.jsonl"
    text = outbox.read_text(encoding="utf-8") if outbox.exists() else ""
    assert "msg-inv-001" not in text
