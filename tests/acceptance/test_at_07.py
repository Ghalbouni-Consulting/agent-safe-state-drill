# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

pytestmark = pytest.mark.acceptance


def test_at_07_safe_state_mid_task_within_the_demo_budget(completed_drill) -> None:
    result = completed_drill.result
    assert result["verdict"] == "PASS"
    assert completed_drill.exit_code == 0
    ids = [step["id"] for step in result["steps"]]
    assert ids == ["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
    assert all(step["pass"] for step in result["steps"])
    assert result["time_to_safe_state_ms"] <= result["budget_ms"]
    task = _task(completed_drill)
    assert task["status"] == "HALTED"
    assert task["step"] == 3


def _task(outcome):
    import json

    return json.loads((outcome.run_dir / "state" / "task.json").read_text(encoding="utf-8"))
