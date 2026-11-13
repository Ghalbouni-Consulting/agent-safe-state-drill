# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Manual fallback. It holds no tokens and does not touch policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksdrill.clock import paris_iso
from ksdrill.util import json_dumps, sha256_file

NOTICE = "your request is in manual processing"


@dataclass
class FallbackReport:
    active: bool
    token_count: int
    policy_sha256: str
    policy_unchanged: bool


class FallbackMode:
    def __init__(self, run_dir: Path, policy_path: Path) -> None:
        self.run_dir = run_dir
        self.queue_path = run_dir / "fallback" / "manual_queue.jsonl"
        self.notice_path = run_dir / "fallback" / "notices.jsonl"
        self.state_path = run_dir / "state" / "fallback.json"
        self.policy_path = policy_path
        self.active = False
        self.token_count = 0
        self.policy_sha256 = sha256_file(policy_path) if policy_path.exists() else ""
        self.baseline_policy_sha256 = self.policy_sha256
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.active = bool(data.get("active"))
            self.token_count = int(data.get("token_count", 0))
            self.baseline_policy_sha256 = data.get("baseline_policy_sha256") or self.policy_sha256

    def _persist(self) -> None:
        payload = {
            "active": self.active,
            "token_count": self.token_count,
            "baseline_policy_sha256": self.baseline_policy_sha256,
            "policy_sha256": sha256_file(self.policy_path),
            "updated": paris_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json_dumps(payload) + "\n", encoding="utf-8")

    def activate(self) -> FallbackReport:
        current = sha256_file(self.policy_path)
        self.active = True
        self.token_count = 0
        self.policy_sha256 = current
        self._persist()
        return FallbackReport(
            active=True,
            token_count=0,
            policy_sha256=current,
            policy_unchanged=current == self.baseline_policy_sha256,
        )

    def deactivate(self) -> None:
        self.active = False
        self._persist()

    def accept_task(self, instruction: str, requester: str | None) -> dict[str, Any]:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        task = {
            "ts": paris_iso(),
            "status": "AWAITING_HUMAN",
            "instruction": instruction,
            "requester": requester or "operator",
        }
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json_dumps(task) + "\n")
        notice = {"ts": paris_iso(), "text": NOTICE, "instruction": instruction}
        with self.notice_path.open("a", encoding="utf-8") as handle:
            handle.write(json_dumps(notice) + "\n")
        return task

    def queue_count(self) -> int:
        if not self.queue_path.exists():
            return 0
        return sum(1 for line in self.queue_path.read_text(encoding="utf-8").splitlines() if line.strip())

    def current_policy_hash(self) -> str:
        return sha256_file(self.policy_path)
