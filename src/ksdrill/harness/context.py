# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""One harness: shared lock, tools, broker, queue, log and stop controller."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ksdrill.config import (
    config_sha256,
    load_clause_map,
    load_deployment,
    load_drill,
    load_policy,
    load_tools,
)
from ksdrill.harness.auditlog import AuditLog
from ksdrill.harness.fallback import FallbackMode
from ksdrill.harness.gateway import Gateway
from ksdrill.harness.outbound import OutboundQueue
from ksdrill.harness.state import SafeStateLatch, TaskState
from ksdrill.harness.token_broker import TokenBroker
from ksdrill.runstore import read_secret
from ksdrill.tools.email_tool import EmailTool
from ksdrill.tools.files_tool import FilesTool
from ksdrill.tools.payments_tool import PaymentsTool
from ksdrill.util import assert_loopback_host


class HarnessContext:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_id = (run_dir / "RUN_ID").read_text(encoding="utf-8").strip()
        self.settings = load_drill(run_dir / "config" / "drill.yaml")
        assert_loopback_host(self.settings.bind_host)
        self.tools_file = load_tools(run_dir / "config" / "tools.yaml")
        self.policy = load_policy(run_dir / "config" / "policy.yaml")
        self.deployment = load_deployment(run_dir / "config" / "deployment_record.yaml")
        self.clause_map = load_clause_map(run_dir / "config" / "clause_map.yaml")
        self.config_sha256 = config_sha256(run_dir / "config")
        self.admin_key = read_secret(run_dir, "admin.key")
        self.signing_key = read_secret(run_dir, "signing.key")
        self.agent_key = read_secret(run_dir, "agent.key")
        self.cv = threading.Condition(threading.RLock())
        self.audit = AuditLog(run_dir, self.run_id)
        self.audit.ban(self.admin_key)
        self.audit.ban(self.signing_key)
        self.audit.ban(self.agent_key)
        self.latch = SafeStateLatch(run_dir / "state" / "latch.json")
        self.task = TaskState(run_dir / "state" / "task.json")
        specs = {spec.id: spec for spec in self.tools_file.tools}
        self.broker = TokenBroker(
            signing_key=self.signing_key,
            bootstrap_key=self.agent_key,
            agent_id=self.settings.agent_id,
            state_path=run_dir / "state" / "broker.json",
            tools=specs,
            policy=self.policy,
            default_ttl=self.settings.token_ttl_s,
        )
        self.files = FilesTool(run_dir / "sandbox")
        self.payments = PaymentsTool(run_dir / "ledger.sqlite")
        self.email = EmailTool(run_dir / "sinks" / "outbox.jsonl")
        self.tool_adapters: dict[str, Any] = {
            "files": self.files,
            "payments": self.payments,
            "email": self.email,
        }
        self.outbound = OutboundQueue(
            state_path=run_dir / "state" / "outbound.json",
            interval_ms=self.settings.dispatcher_interval_ms,
            email_delay_ms=self.settings.email_dispatch_delay_ms,
            deliver=self._deliver,
            audit_append=self.audit.append,
            latch_is_set=lambda: self.latch.is_set,
            condition=self.cv,
        )
        self.fallback = FallbackMode(run_dir, run_dir / "config" / "policy.yaml")
        self.gateway = Gateway(
            tools=self.tool_adapters,
            broker=self.broker,
            outbound=self.outbound,
            policy=self.policy,
            audit_append=self.audit.append,
            latch_is_set=lambda: self.latch.is_set,
            probe_enabled=lambda: self.settings.probe_token_layer,
            condition=self.cv,
            velocity_enabled=self.settings.velocity.enabled,
            velocity_max=self.settings.velocity.max_payments_in_window,
            velocity_window_s=self.settings.velocity.window_s,
        )
        self.gateway.load_idempotency(run_dir / "state" / "idempotency.json")
        self.idem_path = run_dir / "state" / "idempotency.json"
        # Imported lazily to keep the constructor free of a cycle in type hints.
        from ksdrill.harness.stop_controller import StopController

        self.stop = StopController(self)
        self._started = False

    def startup(self) -> None:
        if self._started:
            return
        self._started = True
        if self.latch.is_set:
            self.broker.set_latched(True)
            if not self.outbound.paused:
                self.outbound.pause()
            self.audit.append(
                {
                    "actor": "harness",
                    "event": "harness_up",
                    "decision": "recover",
                    "reason": "preflight_ok",
                    "result": f"recovered_safe_state sha256={self.config_sha256}",
                }
            )
            return
        for adapter in self.tool_adapters.values():
            adapter.connect()
        if not self.fallback.state_path.exists():
            self.fallback._persist()
        self.outbound.start()
        self.audit.append(
            {
                "actor": "harness",
                "event": "harness_up",
                "decision": "allow",
                "reason": "preflight_ok",
                "result": f"sha256={self.config_sha256}",
            }
        )

    def shutdown(self) -> None:
        self.outbound.stop_thread()
        self.audit.close()
        self.payments.disconnect()

    def _deliver(self, item: dict[str, Any]) -> dict[str, Any]:
        if item["channel"] == "bank":
            return self.payments.apply(item["args"], item["idempotency_key"])
        if item["channel"] == "email":
            return self.email.deliver(item["args"], item["idempotency_key"])
        raise RuntimeError(f"unknown channel {item['channel']}")

    def executed_idempotency_keys(self) -> set[str]:
        keys = set(self.payments.idempotency_keys())
        keys.update(self.email.idempotency_keys())
        return keys

    def restore(self, operator: str, note: str) -> dict[str, Any]:
        if not self.latch.is_set:
            return {"ok": False, "error": "NOT_LATCHED"}
        with self.cv:
            epoch = self.broker.enable_for_restore()
            self.broker.set_latched(False)
            self.latch.clear()
            for adapter in self.tool_adapters.values():
                adapter.connect()
            resumed = self.outbound.resume_without_replay(self.executed_idempotency_keys())
            self.fallback.deactivate()
            self.task.mark_resumable()
            self.audit.append(
                {
                    "actor": f"operator:{operator}",
                    "event": "restore",
                    "decision": "allow",
                    "reason": note,
                    "result": f"epoch={epoch};requeued={resumed['requeued']};suppressed={resumed['suppressed']}",
                    "approval": note,
                }
            )
        return {"ok": True, "epoch": epoch, **resumed}

    def status(self) -> dict[str, Any]:
        queue = self.outbound.counts()
        return {
            "run_id": self.run_id,
            "state": self.latch.state,
            "stop_completed": self.latch.stop_completed,
            "epoch": self.broker.epoch,
            "issuing": self.broker.issuing,
            "outstanding_jti": self.broker.outstanding(),
            "tools": {
                name: "CONNECTED" if adapter.connected else "DISCONNECTED"
                for name, adapter in self.tool_adapters.items()
            },
            "queue": queue,
            "task": self.task.as_dict(),
            "fallback_active": self.fallback.active,
            "fallback_token_count": self.fallback.token_count,
            "ledger_count": self.payments.count(),
            "outbox_count": self.email.count(),
            "email_inflight": queue["email_inflight"],
            "config_sha256": self.config_sha256,
            "probe_token_layer": self.settings.probe_token_layer,
            "policy_sha256": self.fallback.current_policy_hash(),
        }
