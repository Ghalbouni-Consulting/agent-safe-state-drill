# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json

import pytest

pytestmark = pytest.mark.acceptance


def test_at_18_second_stop_is_already_safe(live) -> None:
    first = live.admin.post("/admin/stop", json={"operator": "joe", "reason": "first", "trigger": "operator"}).json()
    assert first["pass"] is True
    before = _count_events(live, "stop_received")
    second = live.admin.post("/admin/stop", json={"operator": "joe", "reason": "second", "trigger": "operator"}).json()
    assert second["already_safe"] is True
    assert second["pass"] is True
    assert _count_events(live, "stop_received") == before + 1


def _count_events(live, name: str) -> int:
    count = 0
    for path in (live.run / "log").glob("audit-*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("event") == name:
                count += 1
    return count
