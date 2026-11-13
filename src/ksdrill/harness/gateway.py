# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Default-deny tool gateway. Check order is fixed and every decision is logged."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ksdrill.harness.outbound import OutboundPaused, OutboundQueue
from ksdrill.harness.token_broker import TokenBroker
from ksdrill.models import PolicyFile
from ksdrill.tools.base import PathTraversal, ToolAdapter, ToolError
from ksdrill.tools.files_tool import FilesTool
from ksdrill.util import args_digest, json_dumps


@dataclass
class GateResponse:
    status: int
    code: str
    body: dict[str, Any] = field(default_factory=dict)
    velocity_breach: bool = False
    breach_mono_ns: int | None = None


class Gateway:
    """Order: latch, known tool, token, policy, connected, then dispatch."""

    def __init__(
        self,
        *,
        tools: dict[str, ToolAdapter],
        broker: TokenBroker,
        outbound: OutboundQueue,
        policy: PolicyFile,
        audit_append: Callable[[dict[str, Any]], dict[str, Any]],
        latch_is_set: Callable[[], bool],
        probe_enabled: Callable[[], bool],
        condition: Any,
        velocity_enabled: bool,
        velocity_max: int,
        velocity_window_s: float,
    ) -> None:
        self.tools = tools
        self.broker = broker
        self.outbound = outbound
        self.policy = policy
        self.audit = audit_append
        self.latch_is_set = latch_is_set
        self.probe_enabled = probe_enabled
        self.cv = condition
        self.velocity_enabled = velocity_enabled
        self.velocity_max = velocity_max
        self.velocity_window_s = velocity_window_s
        self._payments_mono: list[float] = []
        self.idempotency: dict[str, dict[str, Any]] = {}

    def load_idempotency(self, path: Any) -> None:
        from pathlib import Path

        file = Path(path)
        if file.exists():
            self.idempotency = json.loads(file.read_text(encoding="utf-8"))

    def save_idempotency(self, path: Any) -> None:
        from pathlib import Path

        from ksdrill.util import atomic_write

        atomic_write(Path(path), json_dumps(self.idempotency) + "\n", fsync=False)

    def handle(
        self,
        *,
        tool_id: str,
        action: str,
        token: str,
        args: dict[str, Any],
        idem_key: str,
        probe: bool,
        actor: str,
        request_id: str | None,
        instruction_ref: str | None,
        idem_path: Any = None,
        internal: bool = False,
    ) -> GateResponse:
        digest = args_digest(args or {})
        breach = False
        breach_ns: int | None = None
        skip_latch = internal or (probe and self.probe_enabled())
        with self.cv:
            if self.latch_is_set() and not skip_latch:
                self._deny(
                    409,
                    "HALTED",
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    None,
                    None,
                )
                return GateResponse(409, "HALTED")
            adapter = self.tools.get(tool_id)
            if adapter is None or action not in adapter.actions:
                self._deny(
                    404,
                    "DENY_UNKNOWN",
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    None,
                    None,
                )
                return GateResponse(404, "DENY_UNKNOWN")
            verdict = self.broker.verify(token, tool_id, action)
            if not verdict.ok:
                status = 403 if verdict.code == "SCOPE_MISMATCH" else 401
                self._deny(
                    status,
                    verdict.code,
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    f"{tool_id}:{action}",
                )
                return GateResponse(status, verdict.code)
            policy_code = self._policy(tool_id, action, args)
            if policy_code:
                self._deny(
                    403,
                    policy_code,
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    f"{tool_id}:{action}",
                )
                return GateResponse(403, policy_code)
            if isinstance(adapter, FilesTool):
                try:
                    adapter._resolve(str(args.get("path", "")))
                except PathTraversal:
                    self._deny(
                        403,
                        "PATH_TRAVERSAL",
                        tool_id,
                        action,
                        actor,
                        request_id,
                        instruction_ref,
                        digest,
                        verdict.jti,
                        f"{tool_id}:{action}",
                    )
                    return GateResponse(403, "PATH_TRAVERSAL")
            if not adapter.connected:
                self._deny(
                    503,
                    "TOOLS_DISCONNECTED",
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    f"{tool_id}:{action}",
                )
                return GateResponse(503, "TOOLS_DISCONNECTED")
            cached = self.idempotency.get(idem_key)
            if cached and cached.get("tool") == tool_id and cached.get("action") == action:
                self._allow(
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    "idempotent_replay",
                    cached.get("result_summary", "replay"),
                )
                return GateResponse(int(cached["status"]), str(cached["code"]), dict(cached["body"]))
            if adapter.outbound:
                try:
                    item = self.outbound.enqueue(
                        channel=adapter.channel or tool_id,
                        tool=tool_id,
                        action=action,
                        args=args,
                        idem_key=idem_key,
                        request_id=request_id,
                        instruction_ref=instruction_ref,
                        token_jti=verdict.jti,
                        actor=actor,
                    )
                except OutboundPaused as exc:
                    code = exc.code
                    status = 409 if code == "HALTED" else 423
                    self._deny(
                        status,
                        code,
                        tool_id,
                        action,
                        actor,
                        request_id,
                        instruction_ref,
                        digest,
                        verdict.jti,
                        f"{tool_id}:{action}",
                    )
                    return GateResponse(status, code)
                body = {"detail": "QUEUED", "queue_id": item["id"], "status": "QUEUED"}
                summary = f"queued:{item['id']}"
                self._remember(idem_key, tool_id, action, 202, "QUEUED", body, summary, idem_path)
                self._allow(
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    "policy:cap_ok",
                    summary,
                )
                if tool_id == "payments":
                    breach, breach_ns = self._note_payment()
                return GateResponse(202, "QUEUED", body, velocity_breach=breach, breach_mono_ns=breach_ns)
            try:
                result = adapter.call(action, args, idem_key)
            except PathTraversal:
                self._deny(
                    403,
                    "PATH_TRAVERSAL",
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    f"{tool_id}:{action}",
                )
                return GateResponse(403, "PATH_TRAVERSAL")
            except ToolError as exc:
                self._deny(
                    exc.status,
                    exc.code,
                    tool_id,
                    action,
                    actor,
                    request_id,
                    instruction_ref,
                    digest,
                    verdict.jti,
                    f"{tool_id}:{action}",
                )
                return GateResponse(exc.status, exc.code)
            summary = _result_summary(tool_id, action, result)
            body = {"detail": "OK", "result": result}
            self._remember(idem_key, tool_id, action, 200, "OK", body, summary, idem_path)
            self._allow(
                tool_id,
                action,
                actor,
                request_id,
                instruction_ref,
                digest,
                verdict.jti,
                "policy:cap_ok",
                summary,
            )
            return GateResponse(200, "OK", body)

    def connection_gate(self, tool_id: str) -> str:
        adapter = self.tools.get(tool_id)
        if adapter is None or not adapter.connected:
            return "TOOLS_DISCONNECTED"
        return "CONNECTED"

    def _policy(self, tool_id: str, action: str, args: dict[str, Any]) -> str | None:
        allowed = self.policy.allow.get(tool_id, [])
        if self.policy.deny_by_default and action not in allowed:
            return "POLICY_DENY"
        if tool_id == "payments" and action == "initiate":
            try:
                amount = float(args.get("amount_eur", 0))
            except (TypeError, ValueError):
                return "POLICY_DENY"
            if amount <= 0 or amount > self.policy.caps.payments.max_amount_eur:
                return "POLICY_DENY"
        return None

    def _note_payment(self) -> tuple[bool, int | None]:
        if not self.velocity_enabled:
            return False, None
        now = time.monotonic()
        self._payments_mono.append(now)
        window = [t for t in self._payments_mono if now - t <= self.velocity_window_s]
        self._payments_mono = window
        if len(window) > self.velocity_max and not self.latch_is_set():
            from ksdrill.clock import monotonic_ns

            return True, monotonic_ns()
        return False, None

    def _remember(
        self,
        idem_key: str,
        tool: str,
        action: str,
        status: int,
        code: str,
        body: dict[str, Any],
        summary: str,
        path: Any,
    ) -> None:
        self.idempotency[idem_key] = {
            "tool": tool,
            "action": action,
            "status": status,
            "code": code,
            "body": body,
            "result_summary": summary,
        }
        if path is not None:
            self.save_idempotency(path)

    def _allow(
        self,
        tool: str,
        action: str,
        actor: str,
        request_id: str | None,
        instruction_ref: str | None,
        digest: str,
        jti: str | None,
        reason: str,
        result: str,
    ) -> None:
        self.audit(
            {
                "actor": actor,
                "event": "tool_call",
                "tool": tool,
                "action": action,
                "request_id": request_id,
                "instruction_ref": instruction_ref,
                "token_jti": jti,
                "scope": f"{tool}:{action}",
                "decision": "allow",
                "reason": reason,
                "args_digest": digest,
                "result": result,
                "approval": "scenario-mandate" if tool in {"payments", "email"} else None,
            }
        )

    def _deny(
        self,
        status: int,
        code: str,
        tool: str,
        action: str,
        actor: str,
        request_id: str | None,
        instruction_ref: str | None,
        digest: str,
        jti: str | None,
        scope: str | None,
    ) -> None:
        del status
        self.audit(
            {
                "actor": actor,
                "event": "tool_call",
                "tool": tool,
                "action": action,
                "request_id": request_id,
                "instruction_ref": instruction_ref,
                "token_jti": jti,
                "scope": scope,
                "decision": "deny",
                "reason": code,
                "args_digest": digest,
                "result": code,
                "approval": None,
            }
        )


def _result_summary(tool: str, action: str, result: dict[str, Any]) -> str:
    if tool == "files":
        return f"ok:{action} {result.get('path')} bytes={result.get('bytes')}"
    if tool == "payments":
        return f"ok:ledger_row={result.get('ledger_row')}"
    return "ok"
