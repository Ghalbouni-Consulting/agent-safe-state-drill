# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Read and write files confined to the run sandbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ksdrill.tools.base import PathTraversal, ToolAdapter, ToolError


class FilesTool(ToolAdapter):
    tool_id = "files"
    actions = ("read", "write")
    outbound = False

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def call(self, action: str, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        del idem_key
        if not self.connected:
            raise ToolError(503, "TOOLS_DISCONNECTED")
        rel = str(args.get("path", ""))
        path = self._resolve(rel)
        if action == "read":
            if not path.is_file():
                raise ToolError(404, "NOT_FOUND")
            data = path.read_text(encoding="utf-8")
            return {"ok": True, "path": path.name, "bytes": len(data.encode("utf-8"))}
        if action == "write":
            content = str(args.get("content", ""))
            path.write_text(content, encoding="utf-8")
            return {"ok": True, "path": path.name, "bytes": len(content.encode("utf-8"))}
        raise ToolError(404, "DENY_UNKNOWN")

    def _resolve(self, rel: str) -> Path:
        if not rel or rel.startswith(("/", "\\")):
            raise PathTraversal()
        parts = Path(rel).parts
        if not parts or ".." in parts:
            raise PathTraversal()
        root = self.root.resolve()
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathTraversal() from exc
        return candidate
