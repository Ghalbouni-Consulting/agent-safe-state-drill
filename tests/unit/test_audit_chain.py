# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ksdrill.harness.auditlog import AuditLog, verify_file
from ksdrill.util import clear_readonly

pytestmark = pytest.mark.unit


def test_chain_roundtrip_and_tamper(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "log").mkdir(parents=True)
    log = AuditLog(run, "r-test")
    first = log.append({"actor": "harness", "event": "harness_up", "decision": "allow", "reason": "preflight_ok"})
    second = log.append({"actor": "agent:ap-agent-01", "event": "tool_call", "tool": "files", "action": "read", "decision": "allow"})
    log.close()
    report = verify_file(run / "log" / "audit-000.jsonl")
    assert report.ok
    assert report.records == 2
    assert report.head_hash == second["hash"]
    assert first["prev_hash"] == "0" * 64
    assert second["prev_hash"] == first["hash"]

    lines = (run / "log" / "audit-000.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["hash"] = ("0" if record["hash"][0] != "0" else "1") + record["hash"][1:]
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    copy = tmp_path / "copy.jsonl"
    copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    broken = verify_file(copy)
    assert not broken.ok
    assert broken.broken_seq == 2
    assert broken.summary == "BROKEN at seq 2"
    clear_readonly(run / "log" / "audit-000.jsonl")


def test_refuses_to_write_a_secret(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "log").mkdir(parents=True)
    log = AuditLog(run, "r-test")
    log.ban("super-secret-value")
    with pytest.raises(RuntimeError):
        log.append({"actor": "x", "event": "tool_call", "reason": "super-secret-value"})
    log.close()
