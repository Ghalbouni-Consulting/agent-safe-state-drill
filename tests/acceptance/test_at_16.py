# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import ast
import time

import psutil
import pytest

from ksdrill.agent.launcher import launch_agent
from ksdrill.config import repo_root
from tests.support import start_live

pytestmark = pytest.mark.acceptance


def test_at_16_admin_key_is_not_in_the_agent_environment(tmp_path) -> None:
    live = start_live(tmp_path, stop_window_ms=0, step_delay_ms=30000)
    proc = None
    try:
        code, proc = launch_agent(
            live.run,
            repo=repo_root(),
            llm="mock",
            extra_env={"KSD_STOP_WINDOW_MS": "0", "KSD_STEP_DELAY_MS": "30000"},
        )
        assert code == 0 and proc is not None
        time.sleep(0.3)
        env = psutil.Process(proc.pid).environ()
        assert not any(key.startswith("KSD_ADMIN") for key in env)
        assert live.admin_key not in "".join(env.values())
        assert "KSD_AGENT_KEY" in env
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        live.close()
    root = repo_root() / "src" / "ksdrill" / "agent"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "harness" not in node.module
