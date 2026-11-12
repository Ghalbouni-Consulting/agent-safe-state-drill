# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Persisted safe-state latch and the current task cursor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ksdrill.clock import monotonic_ns, paris_iso
from ksdrill.util import atomic_write, json_dumps


class SafeStateLatch:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = "RUNNING"
        self.operator: str | None = None
        self.reason: str | None = None
        self.trigger: str | None = None
        self.mono_ns: int | None = None
        self.ts: str | None = None
        self.stop_completed = False
        self.snapshot: dict[str, Any] = {}
        self.seq: int | None = None
        if path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.state = data.get("state", "RUNNING")
        self.operator = data.get("operator")
        self.reason = data.get("reason")
        self.trigger = data.get("trigger")
        self.mono_ns = data.get("mono_ns")
        self.ts = data.get("ts")
        self.stop_completed = bool(data.get("stop_completed"))
        self.snapshot = data.get("snapshot") or {}
        self.seq = data.get("seq")

    @property
    def is_set(self) -> bool:
        return self.state == "SAFE_STATE"

    def trip(
        self,
        *,
        operator: str,
        reason: str,
        trigger: str,
        snapshot: dict[str, Any],
    ) -> bool:
        """Persist SAFE_STATE and the in-memory flag. Returns False if already latched."""
        if self.is_set:
            return False
        self.state = "SAFE_STATE"
        self.operator = operator
        self.reason = reason
        self.trigger = trigger
        self.mono_ns = monotonic_ns()
        self.ts = paris_iso()
        self.snapshot = snapshot
        self._write()
        return True

    def mark_completed(self, seq: int | None) -> None:
        self.stop_completed = True
        self.seq = seq if seq is not None else self.seq
        self._write()

    def note_seq(self, seq: int) -> None:
        self.seq = seq
        self.snapshot["seq"] = seq
        self._write()

    def clear(self) -> None:
        self.state = "RUNNING"
        self.stop_completed = False
        self._write()

    def _write(self) -> None:
        payload = {
            "state": self.state,
            "operator": self.operator,
            "reason": self.reason,
            "trigger": self.trigger,
            "mono_ns": self.mono_ns,
            "ts": self.ts,
            "stop_completed": self.stop_completed,
            "snapshot": self.snapshot,
            "seq": self.seq,
        }
        atomic_write(self.path, json_dumps(payload) + "\n", fsync=True)


class TaskState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.status = "IDLE"
        self.step = 0
        self.phase = "idle"
        self.completed: list[int] = []
        self.idempotency_keys: dict[str, str] = {}
        self.instruction_ref: str | None = None
        self.tool: str | None = None
        self.action: str | None = None
        if path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.status = data.get("status", "IDLE")
        self.step = int(data.get("step") or 0)
        self.phase = data.get("phase", "idle")
        self.completed = [int(x) for x in data.get("completed", [])]
        self.idempotency_keys = {str(k): str(v) for k, v in (data.get("idempotency_keys") or {}).items()}
        self.instruction_ref = data.get("instruction_ref")
        self.tool = data.get("tool")
        self.action = data.get("action")

    def apply_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if self.status == "HALTED" and name != "step_halted":
            # A late observation must not clear the halt. Completion is kept
            # so a later restore knows which steps already ran.
            if name == "step_done":
                index = int(event.get("index") or 0)
                if index and index not in self.completed:
                    self.completed.append(index)
                self._write()
            return
        if name == "step_started":
            self.status = "RUNNING"
            self.step = int(event.get("index") or self.step)
            self.phase = "started"
            self.tool = event.get("tool")
            self.action = event.get("action")
            self.instruction_ref = event.get("instruction_ref")
            if event.get("idempotency_key"):
                self.idempotency_keys[str(self.step)] = str(event["idempotency_key"])
        elif name == "step_done":
            index = int(event.get("index") or self.step)
            self.step = index
            self.phase = "done"
            if index not in self.completed:
                self.completed.append(index)
            self.status = "RUNNING"
        elif name == "step_halted":
            self.status = "HALTED"
            self.phase = "halted"
        elif name == "llm_unreachable":
            self.phase = "llm_unreachable"
        self._write()

    def mark_halted(self) -> None:
        self.status = "HALTED"
        self.phase = "halted"
        self._write()

    def mark_resumable(self) -> None:
        if self.status == "HALTED":
            self.status = "RUNNING"
            self._write()

    def _write(self) -> None:
        payload = {
            "status": self.status,
            "step": self.step,
            "phase": self.phase,
            "completed": self.completed,
            "idempotency_keys": self.idempotency_keys,
            "instruction_ref": self.instruction_ref,
            "tool": self.tool,
            "action": self.action,
        }
        atomic_write(self.path, json.dumps(payload, indent=2) + "\n", fsync=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "step": self.step,
            "phase": self.phase,
            "completed": list(self.completed),
            "idempotency_keys": dict(self.idempotency_keys),
            "instruction_ref": self.instruction_ref,
            "tool": self.tool,
            "action": self.action,
        }
