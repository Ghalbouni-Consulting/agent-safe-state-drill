# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Two FastAPI apps that share one harness. The agent can see only the first."""

from __future__ import annotations

import hmac
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ksdrill.harness.context import HarnessContext


class TokenIn(BaseModel):
    agent_id: str
    tool_id: str
    action: str


class ToolIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    instruction_ref: str | None = None
    request_id: str | None = None


class EventIn(BaseModel):
    event: str
    index: int | None = None
    tool: str | None = None
    action: str | None = None
    instruction_ref: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    detail: str | None = None


class TaskIn(BaseModel):
    instruction: str
    requester: str | None = None


class StopIn(BaseModel):
    operator: str
    reason: str
    trigger: Literal["operator", "supervisor"] = "operator"
    detect_mono_ns: int | None = None


class RestoreIn(BaseModel):
    operator: str
    approve: str


class FreezeIn(BaseModel):
    channels: list[str]


class EmailDelayIn(BaseModel):
    delay_ms: int


class BypassLogIn(BaseModel):
    id: str
    name: str
    observed: str
    passed: bool


def _remember_replay_token(ctx: HarnessContext, tool_id: str, token: str) -> None:
    """Keep the latest token under secrets/ so a later bypass can replay it.

    AT-05 allows secret material only inside secrets/. The audit log never sees it.
    """
    import json

    from ksdrill.util import write_secret

    path = ctx.run_dir / "secrets" / "replay_tokens.json"
    current: dict[str, str] = {}
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
    current[tool_id] = token
    write_secret(path, json.dumps(current))


def create_agent_app(ctx: HarnessContext) -> FastAPI:
    app = FastAPI(title="ksdrill-agent", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "role": "agent", "run_id": ctx.run_id}

    @app.post("/broker/token")
    def issue_token(body: TokenIn, x_agent_key: str = Header(alias="X-Agent-Key")) -> dict[str, Any]:
        with ctx.cv:
            issued = ctx.broker.issue(body.agent_id, x_agent_key, body.tool_id, body.action)
            if issued.ok:
                ctx.audit.ban(issued.token)
                _remember_replay_token(ctx, body.tool_id, issued.token)
                ctx.audit.append(
                    {
                        "actor": f"agent:{body.agent_id}",
                        "event": "token_issued",
                        "tool": body.tool_id,
                        "action": body.action,
                        "token_jti": issued.jti,
                        "scope": issued.scope,
                        "decision": "allow",
                        "reason": "issued",
                        "result": f"exp={issued.exp};epoch={issued.epoch}",
                    }
                )
        if not issued.ok:
            raise HTTPException(issued.status, issued.code)
        return {
            "token": issued.token,
            "jti": issued.jti,
            "exp": issued.exp,
            "scope": issued.scope,
            "epoch": issued.epoch,
        }

    @app.post("/tools/{tool_id}/{action}")
    def call_tool(
        tool_id: str,
        action: str,
        body: ToolIn,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if not idempotency_key:
            raise HTTPException(400, "MISSING_IDEMPOTENCY_KEY")
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "TOKEN_INVALID")
        token = auth.split(" ", 1)[1].strip()
        probe = request.headers.get("x-probe-token-layer") == "1"
        result = ctx.gateway.handle(
            tool_id=tool_id,
            action=action,
            token=token,
            args=body.args,
            idem_key=idempotency_key,
            probe=probe,
            actor=f"agent:{ctx.settings.agent_id}",
            request_id=body.request_id,
            instruction_ref=body.instruction_ref,
            idem_path=ctx.idem_path,
        )
        if result.velocity_breach:
            ctx.stop.stop(
                operator="supervisor",
                reason="velocity threshold: payments in window",
                trigger="supervisor",
                detect_mono_ns=result.breach_mono_ns,
            )
        return JSONResponse(status_code=result.status, content={"detail": result.code, **result.body})

    @app.post("/tasks")
    def post_task(body: TaskIn) -> JSONResponse:
        if ctx.fallback.active:
            task = ctx.fallback.accept_task(body.instruction, body.requester)
            ctx.audit.append(
                {
                    "actor": body.requester or "operator",
                    "event": "fallback_task",
                    "decision": "queue",
                    "reason": "AWAITING_HUMAN",
                    "result": "manual_queue",
                }
            )
            return JSONResponse(status_code=202, content={"detail": "AWAITING_HUMAN", "task": task})
        if ctx.latch.is_set:
            raise HTTPException(409, "HALTED")
        return JSONResponse(status_code=202, content={"detail": "ACCEPTED"})

    @app.post("/agent/events")
    def agent_event(body: EventIn, x_agent_key: str = Header(alias="X-Agent-Key")) -> dict[str, Any]:
        if not hmac.compare_digest(x_agent_key, ctx.agent_key):
            raise HTTPException(401, "AGENT_AUTH")
        ctx.task.apply_event(body.model_dump())
        ctx.audit.append(
            {
                "actor": f"agent:{ctx.settings.agent_id}",
                "event": "agent_step",
                "tool": body.tool,
                "action": body.action,
                "request_id": body.request_id,
                "instruction_ref": body.instruction_ref,
                "decision": body.event,
                "reason": body.detail,
                "result": f"index={body.index};phase={body.event}",
            }
        )
        return {"ok": True}

    @app.get("/agent/task")
    def agent_task(x_agent_key: str = Header(alias="X-Agent-Key")) -> dict[str, Any]:
        if not hmac.compare_digest(x_agent_key, ctx.agent_key):
            raise HTTPException(401, "AGENT_AUTH")
        return ctx.task.as_dict()

    @app.get("/agent/queue/{queue_id}")
    def queue_status(queue_id: str, x_agent_key: str = Header(alias="X-Agent-Key")) -> dict[str, Any]:
        if not hmac.compare_digest(x_agent_key, ctx.agent_key):
            raise HTTPException(401, "AGENT_AUTH")
        with ctx.cv:
            item = ctx.outbound.get(queue_id)
        if item is None:
            raise HTTPException(404, "NOT_FOUND")
        return {"queue_id": queue_id, "status": item["status"]}

    return app


def create_admin_app(ctx: HarnessContext) -> FastAPI:
    app = FastAPI(title="ksdrill-admin", docs_url=None, redoc_url=None, openapi_url=None)

    def require_admin(x_admin_key: str | None) -> None:
        if not x_admin_key or not hmac.compare_digest(x_admin_key, ctx.admin_key):
            raise HTTPException(401, "ADMIN_AUTH")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "role": "admin", "run_id": ctx.run_id}

    @app.post("/admin/stop")
    def admin_stop(body: StopIn, x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        report = ctx.stop.stop(body.operator, body.reason, body.trigger, body.detect_mono_ns)
        return report.model_dump(by_alias=True)

    @app.get("/admin/status")
    def admin_status(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        return ctx.status()

    @app.post("/admin/outbound/resume")
    def resume(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        if ctx.latch.is_set:
            raise HTTPException(409, "HALTED")
        resumed = ctx.outbound.resume_without_replay(ctx.executed_idempotency_keys())
        return {"detail": "RESUMED", **resumed}

    @app.post("/admin/outbound/hold")
    def hold(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        """Drill control: keep queued mail from leaving before the stop."""
        require_admin(x_admin_key)
        ctx.outbound.freeze(["email"])
        return {"detail": "HELD", "channels": ["email"]}

    @app.post("/admin/outbound/email-delay")
    def email_delay(
        body: EmailDelayIn,
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ) -> dict[str, Any]:
        """Live-demo control: how long a queued email stays in flight."""
        require_admin(x_admin_key)
        if body.delay_ms < 0:
            raise HTTPException(400, "BAD_DELAY")
        ctx.outbound.set_email_delay_ms(body.delay_ms)
        return {"detail": "OK", "email_dispatch_delay_ms": ctx.outbound.email_delay_ms}

    @app.post("/admin/outbound/freeze")
    def freeze(body: FreezeIn, x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        ctx.outbound.freeze(body.channels)
        return {"detail": "FROZEN", "channels": body.channels}

    @app.post("/admin/restore")
    def restore(body: RestoreIn, x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        result = ctx.restore(body.operator, body.approve)
        if not result.get("ok"):
            raise HTTPException(409, result.get("error", "NOT_LATCHED"))
        return result

    @app.post("/admin/log/bypass")
    def log_bypass(
        body: BypassLogIn,
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    ) -> dict[str, Any]:
        require_admin(x_admin_key)
        record = ctx.audit.append(
            {
                "actor": "drill",
                "event": "bypass_attempt",
                "action": body.id,
                "decision": "deny" if body.passed else "fail",
                "reason": body.name,
                "result": body.observed[:500],
            }
        )
        return {"seq": record["seq"]}

    @app.get("/admin/log/verify")
    def verify(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> dict[str, Any]:
        require_admin(x_admin_key)
        from ksdrill.harness.auditlog import verify_log_dir

        report = verify_log_dir(ctx.run_dir / "log")
        return {
            "ok": report.ok,
            "records": report.records,
            "head_hash": report.head_hash,
            "broken_seq": report.broken_seq,
            "message": report.summary,
        }

    return app
