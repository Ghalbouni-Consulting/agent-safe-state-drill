# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""LLM adapters. MockLLM is deterministic. Ollama is an optional local model."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ksdrill.models import Scenario, ScenarioStep
from ksdrill.util import assert_loopback


@dataclass
class Action:
    index: int
    tool: str
    action: str
    args: dict[str, Any]
    instruction_ref: str


@dataclass
class Finish:
    reason: str = "scenario_complete"


@dataclass
class Msg:
    role: str
    content: str
    tool: str | None = None
    action: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


class LLM(Protocol):
    def next_action(self, history: list[Msg], tools: list[dict[str, Any]]) -> Action | Finish: ...


class LLMUnreachable(RuntimeError):
    pass


class MockLLM:
    """Replay a scenario. The same tool-call sequence on every run."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self._index = 0

    def next_action(self, history: list[Msg], tools: list[dict[str, Any]]) -> Action | Finish:
        del history, tools
        if self._index >= len(self.scenario.steps):
            return Finish()
        step: ScenarioStep = self.scenario.steps[self._index]
        self._index += 1
        return Action(
            index=step.index,
            tool=step.tool,
            action=step.action,
            args=dict(step.args),
            instruction_ref=step.instruction_ref,
        )


class OllamaLLM:
    """Local Ollama chat API. Refuses any URL that is not loopback."""

    def __init__(self, base_url: str, model: str, timeout_s: float = 20.0, on_unreachable: Any = None) -> None:
        assert_loopback(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.on_unreachable = on_unreachable

    def next_action(self, history: list[Msg], tools: list[dict[str, Any]]) -> Action | Finish:
        deadline = time.monotonic() + self.timeout_s
        notified = False
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self._once(history, tools)
            except (httpx.HTTPError, OSError) as exc:
                last = exc
                if not notified and self.on_unreachable is not None:
                    self.on_unreachable()
                    notified = True
                time.sleep(0.2)
        raise LLMUnreachable(str(last) if last else "timeout")

    def _once(self, history: list[Msg], tools: list[dict[str, Any]]) -> Action | Finish:
        messages = [{"role": "system", "content": "You pay synthetic invoices using the provided tools."}]
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})
        if not history:
            messages.append({"role": "user", "content": "Pay the supplier invoices in the scenario."})
        payload = {"model": self.model, "messages": messages, "tools": tools, "stream": False}
        response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=5.0)
        response.raise_for_status()
        message = response.json().get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return Finish(reason="model_finished")
        call = calls[0].get("function") or {}
        name = str(call.get("name") or "")
        tool, _, action = name.partition("__")
        if not tool or not action:
            return Finish(reason="unparsed_tool_call")
        raw_args = call.get("arguments") or {}
        if isinstance(raw_args, str):
            import json

            raw_args = json.loads(raw_args)
        return Action(index=len(history) + 1, tool=tool, action=action, args=dict(raw_args), instruction_ref="ollama")


def tool_specs_for_llm() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "files__read",
                "description": "Read a sandbox invoice.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "payments__initiate",
                "description": "Pay a synthetic invoice.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "invoice_id": {"type": "string"},
                        "amount_eur": {"type": "number"},
                        "payee": {"type": "string"},
                        "currency": {"type": "string"},
                    },
                    "required": ["invoice_id", "amount_eur", "payee"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "email__send",
                "description": "Queue a synthetic confirmation email.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "message_id": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
    ]
