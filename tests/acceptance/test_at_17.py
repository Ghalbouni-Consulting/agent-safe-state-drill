# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import time

import pytest

from ksdrill.agent.launcher import launch_agent
from ksdrill.config import repo_root
from tests.support import start_live

pytestmark = pytest.mark.acceptance


def test_at_17_stop_passes_when_the_llm_port_is_closed(tmp_path) -> None:
    live = start_live(tmp_path, ollama={"url": "http://127.0.0.1:9", "model": "none", "timeout_s": 20})
    proc = None
    try:
        code, proc = launch_agent(
            live.run,
            repo=repo_root(),
            llm="ollama",
            extra_env={"KSD_OLLAMA_URL": "http://127.0.0.1:9", "KSD_OLLAMA_TIMEOUT": "15"},
        )
        assert proc is not None and code == 0
        time.sleep(0.4)
        report = live.admin.post(
            "/admin/stop",
            json={"operator": "joe", "reason": "llm down", "trigger": "operator"},
        ).json()
        assert report["llm_backend_down"] is True
        assert all(step["pass"] for step in report["steps"])
        assert report["pass"] is True
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        live.close()
