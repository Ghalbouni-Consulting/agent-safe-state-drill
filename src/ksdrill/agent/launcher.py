# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Start the agent in its own process group, with an environment allow-list."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ksdrill.procutil import agent_env, python_module, spawn
from ksdrill.util import atomic_write


def launch_agent(
    run_dir: Path,
    *,
    repo: Path,
    llm: str = "mock",
    scenario: str = "supplier_payments.yaml",
    pause_at_step: int | None = None,
    pause_ms: int = 180_000,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, subprocess.Popen[bytes] | None]:
    """Return (exit code, process). Exit 3 when the safe-state latch is already set.

    When the process is returned, the caller owns it. A blocking wait is done
    only when `wait` is requested via `launch_and_wait`.
    """
    latch_path = run_dir / "state" / "latch.json"
    if latch_path.exists():
        state = json.loads(latch_path.read_text(encoding="utf-8")).get("state")
        if state == "SAFE_STATE":
            print("SAFE_STATE_LATCHED", flush=True)
            return 3, None
    drill = _read_yaml_ports(run_dir)
    agent_key = (run_dir / "secrets" / "agent.key").read_text(encoding="utf-8").strip()
    url = f"http://{drill['bind_host']}:{drill['ports']['agent']}"
    extra = {
        "KSD_AGENT_KEY": agent_key,
        "KSD_AGENT_URL": url,
        "KSD_AGENT_ID": str(drill.get("agent_id", "ap-agent-01")),
        "KSD_RUN_ID": run_dir.name,
        "KSD_SCENARIO_PATH": str(run_dir / "scenarios" / scenario),
        "KSD_LLM": llm,
        "KSD_STEP_DELAY_MS": str(drill.get("step_delay_ms", 0)),
        "KSD_STOP_AT_STEP": str(drill.get("stop_at_step", 0)),
        "KSD_STOP_WINDOW_MS": str(drill.get("stop_window_ms", 0)),
        "KSD_OLLAMA_URL": str((drill.get("ollama") or {}).get("url", "http://127.0.0.1:11434")),
        "KSD_OLLAMA_MODEL": str((drill.get("ollama") or {}).get("model", "llama3.2")),
        "KSD_OLLAMA_TIMEOUT": str((drill.get("ollama") or {}).get("timeout_s", 20)),
        "KSD_REPO_ROOT": str(repo),
    }
    if pause_at_step:
        extra["KSD_PAUSE_AT_STEP"] = str(pause_at_step)
        extra["KSD_PAUSE_MS"] = str(pause_ms)
        # A manual pause replaces the drill's stop window for that step.
        extra["KSD_STOP_WINDOW_MS"] = "0"
    if extra_env:
        extra.update(extra_env)
    env = agent_env(os.environ, extra)
    _write_llm_mode(run_dir, llm, extra["KSD_OLLAMA_URL"])
    proc = spawn(
        python_module("ksdrill.agent.runtime"),
        cwd=repo,
        env=env,
        log_path=run_dir / "agent" / "stdout.log",
    )
    pgid = proc.pid if os.name == "nt" else os.getpgid(proc.pid)
    atomic_write(
        run_dir / "state" / "agent.pid",
        json.dumps({"pid": proc.pid, "pgid": pgid}) + "\n",
        fsync=True,
    )
    return 0, proc


def launch_and_wait(
    run_dir: Path,
    **kwargs: object,
) -> int:
    code, proc = launch_agent(run_dir, **kwargs)  # type: ignore[arg-type]
    if proc is None:
        return code
    status = proc.wait()
    if status != 0 and _latched(run_dir):
        # Stopped by the harness (SIGTERM) after a successful latch.
        return 0
    return status


def _latched(run_dir: Path) -> bool:
    path = run_dir / "state" / "latch.json"
    if not path.exists():
        return False
    return json.loads(path.read_text(encoding="utf-8")).get("state") == "SAFE_STATE"


def _write_llm_mode(run_dir: Path, mode: str, url: str) -> None:
    atomic_write(
        run_dir / "state" / "llm.json",
        json.dumps({"mode": mode, "url": url}) + "\n",
        fsync=True,
    )


def _read_yaml_ports(run_dir: Path) -> dict[str, object]:
    import yaml

    return yaml.safe_load((run_dir / "config" / "drill.yaml").read_text(encoding="utf-8"))
