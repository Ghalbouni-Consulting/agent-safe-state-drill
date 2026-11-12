# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json
import threading
import time

import pytest

pytestmark = pytest.mark.acceptance


def test_at_21_no_allow_after_the_latch_under_load(live) -> None:
    issued = live.issue("files", "read")
    token = issued["body"]["token"]
    stop = threading.Event()

    def hammer(worker: int) -> None:
        n = 0
        while not stop.is_set():
            live.call("files", "read", token, {"path": "INV-001.json"}, f"at21-{worker}-{n}")
            n += 1

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    time.sleep(0.15)
    report = live.admin.post("/admin/stop", json={"operator": "joe", "reason": "velocity", "trigger": "operator"}).json()
    time.sleep(0.2)
    stop.set()
    for thread in threads:
        thread.join(timeout=5)
    assert report["pass"] is True
    latch_seq = _latch_seq(live)
    assert latch_seq is not None
    assert _allows_after(live, latch_seq) == 0


def _latch_seq(live) -> int | None:
    for record in _records(live):
        if record.get("event") == "latch_set":
            return int(record["seq"])
    return None


def _allows_after(live, seq: int) -> int:
    return sum(1 for record in _records(live) if int(record["seq"]) > seq and record.get("decision") == "allow")


def _records(live) -> list[dict]:
    rows = []
    for path in sorted((live.run / "log").glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows
