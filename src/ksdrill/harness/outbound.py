# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Outbound queue. Email and bank submissions leave only through here."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksdrill.clock import monotonic_ns, paris_iso
from ksdrill.util import atomic_write, json_dumps


class OutboundPaused(Exception):
    def __init__(self, code: str = "OUTBOUND_PAUSED") -> None:
        super().__init__(code)
        self.code = code


@dataclass
class PauseReport:
    paused: bool
    held: int
    enqueue_enabled: bool


class OutboundQueue:
    def __init__(
        self,
        *,
        state_path: Path,
        interval_ms: int,
        email_delay_ms: int,
        deliver: Callable[[dict[str, Any]], dict[str, Any]],
        audit_append: Callable[[dict[str, Any]], dict[str, Any]],
        latch_is_set: Callable[[], bool],
        condition: threading.Condition,
    ) -> None:
        self.state_path = state_path
        self.interval = max(interval_ms, 10) / 1000.0
        self.email_delay_ms = email_delay_ms
        self._deliver = deliver
        self._audit = audit_append
        self._latch_is_set = latch_is_set
        self.cv = condition
        self.items: list[dict[str, Any]] = []
        self.paused = False
        self.enqueue_enabled = True
        self.frozen_channels: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if state_path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.items = list(data.get("items") or [])
        self.paused = bool(data.get("paused"))
        self.enqueue_enabled = bool(data.get("enqueue_enabled", not self.paused))
        self.frozen_channels = set(data.get("frozen_channels") or [])

    def _persist(self) -> None:
        payload = {
            "paused": self.paused,
            "enqueue_enabled": self.enqueue_enabled,
            "frozen_channels": sorted(self.frozen_channels),
            "items": self.items,
            "updated": paris_iso(),
        }
        atomic_write(self.state_path, json_dumps(payload) + "\n", fsync=True)

    def set_email_delay_ms(self, delay_ms: int) -> None:
        """Change how long a queued email waits before it can be dispatched."""
        with self.cv:
            self.email_delay_ms = max(0, int(delay_ms))
            self.cv.notify_all()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ksdrill-dispatch", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.drain()

    def freeze(self, channels: list[str]) -> None:
        with self.cv:
            self.frozen_channels.update(channels)
            self._persist()

    def enqueue(
        self,
        *,
        channel: str,
        tool: str,
        action: str,
        args: dict[str, Any],
        idem_key: str,
        request_id: str | None,
        instruction_ref: str | None,
        token_jti: str | None,
        actor: str,
    ) -> dict[str, Any]:
        with self.cv:
            if self._latch_is_set():
                raise OutboundPaused("HALTED")
            if self.paused or not self.enqueue_enabled:
                raise OutboundPaused("OUTBOUND_PAUSED")
            item = {
                "id": "q-" + uuid.uuid4().hex[:16],
                "channel": channel,
                "tool": tool,
                "action": action,
                "args": args,
                "idempotency_key": idem_key,
                "request_id": request_id,
                "instruction_ref": instruction_ref,
                "token_jti": token_jti,
                "actor": actor,
                "status": "QUEUED",
                "enqueued_mono_ns": monotonic_ns(),
                "ts": paris_iso(),
            }
            self.items.append(item)
            self._persist()
            self._audit(
                {
                    "actor": actor,
                    "event": "outbound_enqueued",
                    "tool": tool,
                    "action": action,
                    "request_id": request_id,
                    "instruction_ref": instruction_ref,
                    "token_jti": token_jti,
                    "scope": f"{tool}:{action}",
                    "decision": "allow",
                    "reason": "queued",
                    "result": f"queue_id={item['id']};channel={channel}",
                    "approval": "scenario-mandate" if tool in {"payments", "email"} else None,
                }
            )
            return dict(item)

    def drain(self) -> int:
        sent = 0
        with self.cv:
            if self.paused or self._latch_is_set():
                return 0
            now = monotonic_ns()
            for item in self.items:
                if item["status"] != "QUEUED":
                    continue
                if item["channel"] in self.frozen_channels:
                    continue
                if not self._eligible(item, now):
                    continue
                item["status"] = "DISPATCHING"
                try:
                    result = self._deliver(item)
                except Exception as exc:  # demo sink errors stay in the log, item returns to queue
                    item["status"] = "QUEUED"
                    item["last_error"] = type(exc).__name__
                    continue
                item["status"] = "DISPATCHED"
                item["deliver_result"] = {
                    "replayed": bool(result.get("replayed")),
                    "message_id": result.get("message_id"),
                    "ledger_row": result.get("ledger_row"),
                }
                self._audit(
                    {
                        "actor": "harness",
                        "event": "outbound_dispatched",
                        "tool": item["tool"],
                        "action": item["action"],
                        "request_id": item.get("request_id"),
                        "instruction_ref": item.get("instruction_ref"),
                        "token_jti": item.get("token_jti"),
                        "scope": f"{item['tool']}:{item['action']}",
                        "decision": "allow",
                        "reason": "dispatched",
                        "result": json_dumps(item["deliver_result"]),
                        "approval": "scenario-mandate" if item["tool"] in {"payments", "email"} else None,
                    }
                )
                sent += 1
            if sent:
                self._persist()
            self.cv.notify_all()
        return sent

    def _eligible(self, item: dict[str, Any], now_ns: int) -> bool:
        delay_ms = self.email_delay_ms if item["channel"] == "email" else 0
        return now_ns >= int(item["enqueued_mono_ns"]) + delay_ms * 1_000_000

    def wait_until_settled(self, queue_id: str, timeout: float) -> dict[str, Any] | None:
        end = monotonic_ns() + int(timeout * 1_000_000_000)
        with self.cv:
            while True:
                item = self.get(queue_id)
                if item is None:
                    return None
                if item["status"] in {"DISPATCHED", "HELD", "SUPPRESSED"}:
                    return dict(item)
                remaining_ns = end - monotonic_ns()
                if remaining_ns <= 0:
                    return dict(item)
                self.cv.wait(remaining_ns / 1_000_000_000)

    def get(self, queue_id: str) -> dict[str, Any] | None:
        for item in self.items:
            if item["id"] == queue_id:
                return item
        return None

    def pause(self) -> PauseReport:
        with self.cv:
            self.paused = True
            self.enqueue_enabled = False
            held = 0
            for item in self.items:
                if item["status"] in {"QUEUED", "DISPATCHING"}:
                    item["status"] = "HELD"
                    held += 1
            self._persist()
            self.cv.notify_all()
            return PauseReport(paused=True, held=held, enqueue_enabled=False)

    def resume_without_replay(self, executed_keys: set[str]) -> dict[str, int]:
        with self.cv:
            self.paused = False
            self.enqueue_enabled = True
            self.frozen_channels.clear()
            requeued = 0
            suppressed = 0
            for item in self.items:
                if item["status"] != "HELD":
                    continue
                if item["idempotency_key"] in executed_keys:
                    item["status"] = "SUPPRESSED"
                    suppressed += 1
                else:
                    item["status"] = "QUEUED"
                    # Already waited before the stop; do not restart the dispatch delay.
                    item["enqueued_mono_ns"] = monotonic_ns() - (self.email_delay_ms + 1000) * 1_000_000
                    requeued += 1
            self._persist()
            self.cv.notify_all()
        self.start()
        return {"requeued": requeued, "suppressed": suppressed}

    def counts(self) -> dict[str, int]:
        with self.cv:
            queued = sum(1 for i in self.items if i["status"] == "QUEUED")
            held = sum(1 for i in self.items if i["status"] == "HELD")
            dispatched = sum(1 for i in self.items if i["status"] == "DISPATCHED")
            email_inflight = sum(
                1
                for i in self.items
                if i["channel"] == "email" and i["status"] in {"QUEUED", "DISPATCHING", "HELD"}
            )
            return {
                "queued": queued,
                "held": held,
                "dispatched": dispatched,
                "email_inflight": email_inflight,
            }

    def queued_at_snapshot(self) -> int:
        with self.cv:
            return sum(1 for i in self.items if i["status"] in {"QUEUED", "DISPATCHING"})
