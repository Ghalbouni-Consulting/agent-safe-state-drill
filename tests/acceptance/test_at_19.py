# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json

import pytest

from ksdrill.util import sha256_file

pytestmark = pytest.mark.acceptance


def test_at_19_fallback_takes_work_and_does_not_widen_rights(live) -> None:
    policy = sha256_file(live.run / "config" / "policy.yaml")
    report = live.admin.post("/admin/stop", json={"operator": "joe", "reason": "fallback", "trigger": "operator"}).json()
    assert report["pass"] is True
    response = live.http.post("/tasks", json={"instruction": "pay INV-999", "requester": "teller"})
    assert response.status_code == 202
    queue = (live.run / "fallback" / "manual_queue.jsonl").read_text(encoding="utf-8")
    assert "pay INV-999" in queue
    assert "AWAITING_HUMAN" in queue
    status = live.admin.get("/admin/status").json()
    assert status["fallback_active"] is True
    assert status["fallback_token_count"] == 0
    assert sha256_file(live.run / "config" / "policy.yaml") == policy
    assert status["policy_sha256"] == policy
    notices = (live.run / "fallback" / "notices.jsonl").read_text(encoding="utf-8")
    assert "your request is in manual processing" in notices
    # The predicate canary is also there; the caller's task is the one above.
    assert json.loads(queue.splitlines()[-1])["instruction"] == "pay INV-999"
