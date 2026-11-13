# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Subprocess helpers for the harness and the agent. Loopback only."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

# The agent environment is an allow-list. The admin key must not ride along
# via the parent environment, whatever it is called.
_EXACT = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
}
_PREFIXES = (
    "KSD_AGENT_",
    "KSD_RUN_ID",
    "KSD_SCENARIO",
    "KSD_LLM",
    "KSD_STOP_",
    "KSD_PAUSE_",
    "KSD_STEP_",
    "KSD_OLLAMA_",
    "KSD_REPO_",
)


def agent_env(parent: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in parent.items() if key in _EXACT or key.startswith(_PREFIXES)}
    for key, value in extra.items():
        if key.startswith("KSD_ADMIN") or "SIGNING" in key or "ADMIN" in key:
            raise ValueError(f"refusing to place {key} in the agent environment")
        env[key] = value
    env["PYTHONUNBUFFERED"] = "1"
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_http(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last = "not-started"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=0.4)
            if response.status_code == 200:
                return
            last = f"status {response.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc.__class__.__name__)
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {url}: {last}")


def spawn(cmd: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    proc._log_handle = handle  # type: ignore[attr-defined]
    return proc


def stop_process(proc: subprocess.Popen[bytes] | None, timeout: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        _close_log(proc)
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)
    _close_log(proc)


def _close_log(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    handle = getattr(proc, "_log_handle", None)
    if handle is not None:
        handle.close()


def python_module(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
