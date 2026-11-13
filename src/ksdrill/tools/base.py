# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Tool adapters live inside the harness. The agent never imports them."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class PathTraversal(ToolError):
    def __init__(self) -> None:
        super().__init__(403, "PATH_TRAVERSAL")


class ToolAdapter:
    tool_id: str
    actions: tuple[str, ...]
    outbound: bool = False
    channel: str | None = None

    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def call(self, action: str, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        raise NotImplementedError
