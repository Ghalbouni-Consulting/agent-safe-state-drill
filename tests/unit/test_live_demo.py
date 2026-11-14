# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

from __future__ import annotations

import inspect
import json

import pytest
import yaml
from typer.models import OptionInfo

from ksdrill.cli import _apply_email_delay, _resolve_email_delay, _set_probe, harness_up
from ksdrill.config import load_drill, repo_root
from ksdrill.evidence.card import CardError, build_card, card_model
from ksdrill.harness.context import HarnessContext
from ksdrill.runstore import init_run

pytestmark = pytest.mark.unit


def test_live_email_delay_default_is_one_minute() -> None:
    drill = load_drill(repo_root() / "config" / "drill.yaml")
    assert drill.email_dispatch_delay_ms == 60000


def test_harness_up_enables_the_token_probe_by_default() -> None:
    default = inspect.signature(harness_up).parameters["probe_token_layer"].default
    assert isinstance(default, OptionInfo)
    assert default.default is True


def test_set_probe_writes_the_run_config(tmp_path) -> None:
    run = init_run(tmp_path / "runs")
    _set_probe(run, True)
    assert _probe(run) is True
    _set_probe(run, False)
    assert _probe(run) is False


def test_email_delay_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KSD_EMAIL_DELAY_MS", "15000")
    assert _resolve_email_delay(120000) == 120000
    assert _resolve_email_delay(None) == 15000
    monkeypatch.delenv("KSD_EMAIL_DELAY_MS")
    assert _resolve_email_delay(None) is None


def test_email_delay_is_saved_when_the_harness_is_down(tmp_path) -> None:
    run = init_run(tmp_path / "runs")
    # Point at a closed port so the admin call cannot hit another process.
    text = (run / "config" / "drill.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    data["ports"] = {"agent": 1, "admin": 1}
    (run / "config" / "drill.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _apply_email_delay(run, 120000)
    saved = yaml.safe_load((run / "config" / "drill.yaml").read_text(encoding="utf-8"))
    assert saved["email_dispatch_delay_ms"] == 120000


def test_card_refuses_a_run_that_was_never_stopped(tmp_path) -> None:
    run = init_run(tmp_path / "runs")
    with pytest.raises(CardError, match="stop_report.json"):
        card_model(run)


def test_card_builds_from_a_live_stop_without_drill_result(tmp_path) -> None:
    run = init_run(tmp_path / "runs")
    ctx = HarnessContext(run)
    ctx.startup()
    try:
        report = ctx.stop.stop("joe", "live card", "operator")
        assert report.pass_
    finally:
        ctx.shutdown()
    html_path = build_card(run, write_pdf_file=False)
    html = html_path.read_text(encoding="utf-8")
    assert "Synthetic demo agent and example tools." in html
    assert "S1" in html and "halt_task" in html
    assert "not recorded" in html
    assert "not checked" in html
    assert "No bypass rows" in html
    stored = json.loads((run / "drill_result.json").read_text(encoding="utf-8"))
    assert stored["source"] == "live"
    assert stored["verdict"] == "PASS"
    assert card_model(run)["verdict"] == "PASS"


def _probe(run) -> bool:
    data = yaml.safe_load((run / "config" / "drill.yaml").read_text(encoding="utf-8"))
    return bool(data["probe_token_layer"])
