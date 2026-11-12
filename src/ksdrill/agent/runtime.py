# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Hand-written agent loop: ask the model, take a token, call the gateway, observe.

The loop talks only to the agent API. It does not know the admin port.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ksdrill.agent.llm import (
    Action,
    Finish,
    LLMUnreachable,
    MockLLM,
    Msg,
    OllamaLLM,
    tool_specs_for_llm,
)
from ksdrill.config import load_scenario
from ksdrill.util import assert_loopback


def main() -> None:
    try:
        code = run()
    except LLMUnreachable as exc:
        print(f"llm unreachable: {exc.__class__.__name__}", flush=True)
        code = 2
    raise SystemExit(code)


def run() -> int:
    base = os.environ["KSD_AGENT_URL"]
    assert_loopback(base)
    key = os.environ["KSD_AGENT_KEY"]
    agent_id = os.environ.get("KSD_AGENT_ID", "ap-agent-01")
    scenario = load_scenario(Path(os.environ["KSD_SCENARIO_PATH"]))
    mode = os.environ.get("KSD_LLM", "mock")
    step_delay = int(os.environ.get("KSD_STEP_DELAY_MS", "0"))
    stop_at = int(os.environ.get("KSD_STOP_AT_STEP", "0"))
    stop_window = int(os.environ.get("KSD_STOP_WINDOW_MS", "0"))
    pause_at = int(os.environ.get("KSD_PAUSE_AT_STEP", "0"))
    pause_ms = int(os.environ.get("KSD_PAUSE_MS", "180000"))
    headers = {"X-Agent-Key": key}
    history: list[Msg] = []
    notified = {"done": False}

    def on_unreachable() -> None:
        if notified["done"]:
            return
        notified["done"] = True
        try:
            client.post(
                "/agent/events",
                headers=headers,
                json={"event": "llm_unreachable", "detail": "ollama"},
                timeout=2.0,
            )
        except httpx.HTTPError:
            pass

    if mode == "ollama":
        url = os.environ.get("KSD_OLLAMA_URL", "http://127.0.0.1:11434")
        model = os.environ.get("KSD_OLLAMA_MODEL", "llama3.2")
        timeout = float(os.environ.get("KSD_OLLAMA_TIMEOUT", "20"))
        llm = OllamaLLM(url, model, timeout_s=timeout, on_unreachable=on_unreachable)
    elif mode == "mock":
        llm = MockLLM(scenario)
    else:
        print(f"unknown llm mode: {mode}", flush=True)
        return 2

    client = httpx.Client(base_url=base, timeout=30.0)
    tools = tool_specs_for_llm()
    try:
        while True:
            outcome = llm.next_action(history, tools)
            if isinstance(outcome, Finish):
                print(f"finish {outcome.reason}", flush=True)
                return 0
            code = _one_step(
                client,
                headers,
                agent_id,
                scenario.id,
                outcome,
                history,
            )
            if code is not None:
                return code
            _pause_after(outcome.index, stop_at, stop_window, pause_at, pause_ms, step_delay)
    finally:
        client.close()


def _one_step(
    client: httpx.Client,
    headers: dict[str, str],
    agent_id: str,
    scenario_id: str,
    action: Action,
    history: list[Msg],
) -> int | None:
    idem = f"{scenario_id}:{action.index}"
    request_id = f"task-1/step-{action.index}"
    _event(
        client,
        headers,
        {
            "event": "step_started",
            "index": action.index,
            "tool": action.tool,
            "action": action.action,
            "instruction_ref": action.instruction_ref,
            "request_id": request_id,
            "idempotency_key": idem,
        },
    )
    token_response = client.post(
        "/broker/token",
        headers=headers,
        json={"agent_id": agent_id, "tool_id": action.tool, "action": action.action},
    )
    if token_response.status_code != 200:
        detail = _detail(token_response)
        print(f"token {token_response.status_code} {detail}", flush=True)
        return 4 if token_response.status_code in {401, 403} else 1
    body = token_response.json()
    token = str(body["token"])
    print(f"token 200 jti={body.get('jti')}", flush=True)
    call = client.post(
        f"/tools/{action.tool}/{action.action}",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idem},
        json={
            "args": action.args,
            "instruction_ref": action.instruction_ref,
            "request_id": request_id,
        },
    )
    print(f"step {action.index} {action.tool}.{action.action} -> {call.status_code} {_detail(call)}", flush=True)
    if call.status_code == 409:
        _event(client, headers, {"event": "step_halted", "index": action.index, "request_id": request_id})
        return 0
    if call.status_code == 202 and action.tool == "payments":
        queue_id = str(call.json().get("queue_id", ""))
        status = _wait_queue(client, headers, queue_id)
        print(f"payment {queue_id} {status}", flush=True)
        if status == "HELD":
            _event(client, headers, {"event": "step_halted", "index": action.index, "request_id": request_id})
            return 0
    if call.status_code not in {200, 202}:
        return 1
    history.append(
        Msg(
            role="assistant",
            content=f"{action.tool}.{action.action} -> {call.status_code}",
            tool=action.tool,
            action=action.action,
            args=dict(action.args),
        )
    )
    _event(
        client,
        headers,
        {
            "event": "step_done",
            "index": action.index,
            "tool": action.tool,
            "action": action.action,
            "instruction_ref": action.instruction_ref,
            "request_id": request_id,
            "idempotency_key": idem,
        },
    )
    return None


def _wait_queue(client: httpx.Client, headers: dict[str, str], queue_id: str) -> str:
    deadline = time.monotonic() + 10
    status = "QUEUED"
    while time.monotonic() < deadline:
        response = client.get(f"/agent/queue/{queue_id}", headers=headers)
        if response.status_code != 200:
            return status
        status = str(response.json().get("status", status))
        if status in {"DISPATCHED", "HELD", "SUPPRESSED"}:
            return status
        time.sleep(0.02)
    return status


def _pause_after(
    index: int,
    stop_at: int,
    stop_window: int,
    pause_at: int,
    pause_ms: int,
    step_delay: int,
) -> None:
    if pause_at and index == pause_at:
        _sleep_ms(pause_ms)
    elif stop_at and index == stop_at and stop_window:
        _sleep_ms(stop_window)
    elif step_delay:
        _sleep_ms(step_delay)


def _sleep_ms(millis: int) -> None:
    end = time.monotonic() + millis / 1000.0
    while time.monotonic() < end:
        time.sleep(min(0.05, end - time.monotonic()))


def _event(client: httpx.Client, headers: dict[str, str], payload: dict[str, object]) -> None:
    try:
        client.post("/agent/events", headers=headers, json=payload)
    except httpx.HTTPError:
        pass


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail")
    return str(detail) if detail else ""


if __name__ == "__main__":
    main()
