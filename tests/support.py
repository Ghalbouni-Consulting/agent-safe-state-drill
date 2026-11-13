# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Shared harness bootstrap for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ksdrill.config import repo_root
from ksdrill.procutil import free_port, python_module, spawn, stop_process, wait_http
from ksdrill.runstore import init_run, read_secret


@dataclass
class Live:
    run: Path
    agent_url: str
    admin_url: str
    admin_key: str
    agent_key: str
    proc: Any
    http: httpx.Client
    admin: httpx.Client
    _log: Any = field(default=None, repr=False)

    def close(self) -> None:
        self.http.close()
        self.admin.close()
        stop_process(self.proc)

    def issue(self, tool: str, action: str, agent_id: str = "ap-agent-01") -> dict[str, Any]:
        response = self.http.post(
            "/broker/token",
            headers={"X-Agent-Key": self.agent_key},
            json={"agent_id": agent_id, "tool_id": tool, "action": action},
        )
        return {"status": response.status_code, "body": _json(response)}

    def call(
        self,
        tool: str,
        action: str,
        token: str,
        args: dict[str, Any],
        idem: str,
        *,
        probe: bool = False,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": idem}
        if probe:
            headers["X-Probe-Token-Layer"] = "1"
        response = self.http.post(
            f"/tools/{tool}/{action}",
            headers=headers,
            json={"args": args, "request_id": idem},
        )
        return {"status": response.status_code, "body": _json(response)}


def start_live(tmp_path: Path, **overrides: Any) -> Live:
    ports = overrides.pop("ports", None) or {"agent": free_port(), "admin": free_port()}
    merged: dict[str, Any] = {
        "ports": ports,
        "probe_token_layer": True,
        "dispatcher_interval_ms": 30,
        "step_delay_ms": 0,
        "stop_window_ms": 4000,
        "email_dispatch_delay_ms": 8000,
    }
    merged.update(overrides)
    tool_ttl = merged.pop("tool_ttl_s", None)
    run = init_run(tmp_path / "runs", repo=repo_root(), overrides=merged)
    if tool_ttl is not None:
        _patch_tool_ttl(run, int(tool_ttl))
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONUNBUFFERED"] = "1"
    env["KSD_REPO_ROOT"] = str(repo_root())
    proc = spawn(
        python_module("ksdrill.harness.server", str(run)),
        cwd=repo_root(),
        env=env,
        log_path=run / "agent" / "harness.log",
    )
    host = "127.0.0.1"
    agent_url = f"http://{host}:{ports['agent']}"
    admin_url = f"http://{host}:{ports['admin']}"
    try:
        wait_http(agent_url + "/health")
        wait_http(admin_url + "/health")
    except Exception:
        stop_process(proc)
        log = run / "agent" / "harness.log"
        detail = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        raise RuntimeError(detail) from None
    admin_key = read_secret(run, "admin.key")
    return Live(
        run=run,
        agent_url=agent_url,
        admin_url=admin_url,
        admin_key=admin_key,
        agent_key=read_secret(run, "agent.key"),
        proc=proc,
        http=httpx.Client(base_url=agent_url, timeout=10),
        admin=httpx.Client(base_url=admin_url, timeout=30, headers={"X-Admin-Key": admin_key}),
    )


def run_scenario(tmp_path: Path, **overrides: Any) -> Live:
    """Start a harness and let the mock agent finish the scenario. Caller closes it."""
    live = start_live(tmp_path, stop_window_ms=0, step_delay_ms=0, **overrides)
    from ksdrill.agent.launcher import launch_and_wait

    code = launch_and_wait(
        live.run,
        repo=repo_root(),
        llm="mock",
        extra_env={"KSD_STOP_WINDOW_MS": "0", "KSD_STEP_DELAY_MS": "0", "KSD_PAUSE_AT_STEP": "0"},
    )
    if code != 0:
        detail = (live.run / "agent" / "stdout.log").read_text(encoding="utf-8", errors="replace")
        live.close()
        raise RuntimeError(f"agent exit {code}\n{detail}")
    return live


def tool_call_digest(run: Path) -> list[tuple[str, str, str]]:
    import json

    rows = []
    for name in ("audit-000.jsonl", "audit-001.jsonl"):
        path = run / "log" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("event") == "tool_call" and record.get("decision") == "allow":
                rows.append((record.get("tool"), record.get("action"), record.get("args_digest")))
    return rows


def _patch_tool_ttl(run: Path, ttl: int) -> None:
    import yaml

    path = run / "config" / "tools.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for tool in data["tools"]:
        tool["ttl_s"] = ttl
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {"raw": response.text}
    return body if isinstance(body, dict) else {"value": body}
