# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json
import sqlite3

import pytest

from ksdrill.agent.launcher import launch_and_wait
from ksdrill.config import repo_root
from ksdrill.drill.runner import DrillOptions, run_drill
from ksdrill.procutil import free_port, python_module, spawn, stop_process, wait_http

pytestmark = pytest.mark.acceptance


def test_at_22_restore_does_not_replay_executed_actions(tmp_path) -> None:
    ports = (free_port(), free_port())
    outcome = run_drill(
        DrillOptions(
            runs_root=tmp_path / "runs",
            ports=ports,
            no_pdf=True,
            step_delay_ms=0,
            stop_window_ms=4000,
            dispatcher_interval_ms=30,
            extra_overrides={"email_dispatch_delay_ms": 0},
        )
    )
    assert outcome.result["verdict"] == "PASS"
    run = outcome.run_dir
    env = dict(**{key: value for key, value in __import__("os").environ.items()})
    env["PYTHONUNBUFFERED"] = "1"
    env["KSD_REPO_ROOT"] = str(repo_root())
    proc = spawn(
        python_module("ksdrill.harness.server", str(run)),
        cwd=repo_root(),
        env=env,
        log_path=run / "agent" / "harness-restore.log",
    )
    try:
        wait_http(f"http://127.0.0.1:{ports[0]}/health")
        import httpx

        from ksdrill.runstore import read_secret

        admin = httpx.Client(
            base_url=f"http://127.0.0.1:{ports[1]}",
            headers={"X-Admin-Key": read_secret(run, "admin.key")},
            timeout=30,
        )
        restored = admin.post("/admin/restore", json={"operator": "joe", "approve": "drill resume"}).json()
        assert restored["ok"] is True
        code = launch_and_wait(
            run,
            repo=repo_root(),
            llm="mock",
            extra_env={"KSD_STOP_WINDOW_MS": "0", "KSD_STEP_DELAY_MS": "0"},
        )
        assert code == 0, (run / "agent" / "stdout.log").read_text(encoding="utf-8", errors="replace")
        keys = _ledger_keys(run)
        assert len(keys) == len(set(keys)) == 3
        messages = _wait_messages(run, 3)
        assert len(messages) == len(set(messages))
        assert "msg-inv-001" in messages
        admin.close()
    finally:
        stop_process(proc)


def _ledger_keys(run) -> list[str]:
    conn = sqlite3.connect(run / "ledger.sqlite")
    try:
        return [row[0] for row in conn.execute("SELECT idempotency_key FROM payments")]
    finally:
        conn.close()


def _message_ids(run) -> list[str]:
    path = run / "sinks" / "outbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["message_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_messages(run, count: int) -> list[str]:
    import time

    deadline = time.monotonic() + 10
    messages: list[str] = []
    while time.monotonic() < deadline:
        messages = _message_ids(run)
        if len(messages) >= count:
            return messages
        time.sleep(0.05)
    return messages
