# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json

import pytest

from ksdrill.evidence.card import render_html

pytestmark = pytest.mark.acceptance


def test_at_23_velocity_triggers_a_supervisor_stop(tmp_path) -> None:
    from tests.support import start_live

    live = start_live(
        tmp_path,
        velocity={"enabled": True, "max_payments_in_window": 2, "window_s": 10},
    )
    try:
        for index in range(3):
            issued = live.issue("payments", "initiate")
            assert issued["status"] == 200, issued
            call = live.call(
                "payments",
                "initiate",
                issued["body"]["token"],
                {"invoice_id": f"V-{index}", "amount_eur": 10, "payee": "x", "currency": "EUR"},
                f"at23-{index}",
            )
            # The third call is the breach: it is accepted, then the supervisor stops.
            if index < 2:
                assert call["status"] == 202, call
        report = json.loads((live.run / "stop_report.json").read_text(encoding="utf-8"))
        assert report["trigger"] == "supervisor"
        assert report["detection_time_ms"] is not None
        assert report["pass"] is True
        status = live.admin.get("/admin/status").json()
        assert status["state"] == "SAFE_STATE"
        html = render_html(_card_from_stop(live, report), 7)
        assert "detection" in html
    finally:
        live.close()


def _card_from_stop(live, report: dict) -> dict:
    from tests.unit.test_config_and_clauses import _synthetic_model

    model = _synthetic_model()
    model["detection_time_ms"] = report["detection_time_ms"]
    model["time_to_safe_state_ms"] = report["time_to_safe_state_ms"]
    model["trigger"] = "supervisor"
    model["steps"] = [
        {
            "id": step["id"],
            "name": step["name"],
            "duration_ms": step["duration_ms"],
            "pass": step["pass"],
            "predicate": "ok",
            "clauses": step["clauses"],
        }
        for step in report["steps"]
    ]
    return model
