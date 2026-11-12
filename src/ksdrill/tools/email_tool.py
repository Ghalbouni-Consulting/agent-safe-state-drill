# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Outbox sink. The dispatcher is the only writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ksdrill.clock import paris_iso
from ksdrill.tools.base import ToolAdapter, ToolError
from ksdrill.util import json_dumps


class EmailTool(ToolAdapter):
    tool_id = "email"
    actions = ("send",)
    outbound = True
    channel = "email"

    def __init__(self, outbox: Path) -> None:
        super().__init__()
        self.outbox = outbox
        self.outbox.parent.mkdir(parents=True, exist_ok=True)

    def deliver(self, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        if not self.connected:
            raise ToolError(503, "TOOLS_DISCONNECTED")
        message_id = str(args.get("message_id") or idem_key)
        existing = self.message_ids()
        if message_id in existing or idem_key in self.idempotency_keys():
            return {"replayed": True, "message_id": message_id}
        record = {
            "message_id": message_id,
            "idempotency_key": idem_key,
            "to": args.get("to"),
            "subject": args.get("subject"),
            "dispatched_ts": paris_iso(),
        }
        with self.outbox.open("a", encoding="utf-8") as handle:
            handle.write(json_dumps(record) + "\n")
        return {"replayed": False, "message_id": message_id}

    def count(self) -> int:
        return len(self._rows())

    def message_ids(self) -> set[str]:
        return {str(row.get("message_id")) for row in self._rows() if row.get("message_id")}

    def idempotency_keys(self) -> set[str]:
        return {str(row.get("idempotency_key")) for row in self._rows() if row.get("idempotency_key")}

    def _rows(self) -> list[dict[str, Any]]:
        if not self.outbox.exists():
            return []
        rows = []
        for line in self.outbox.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def call(self, action: str, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        if action != "send":
            raise ToolError(404, "DENY_UNKNOWN")
        return self.deliver(args, idem_key)
