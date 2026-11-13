# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

from __future__ import annotations

from pathlib import Path

import pytest

from ksdrill.drill.runner import DrillOptions, DrillOutcome, run_drill
from ksdrill.procutil import free_port
from tests.support import Live, start_live


@pytest.fixture
def live(tmp_path: Path) -> Live:
    harness = start_live(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


@pytest.fixture(scope="module")
def completed_drill(tmp_path_factory: pytest.TempPathFactory) -> DrillOutcome:
    root = tmp_path_factory.mktemp("drill")
    return run_drill(
        DrillOptions(
            runs_root=root,
            ports=(free_port(), free_port()),
            no_pdf=True,
            step_delay_ms=0,
            stop_window_ms=5000,
            dispatcher_interval_ms=30,
            budget_ms=2000,
        )
    )
