# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

from __future__ import annotations

import ast

import pytest

from ksdrill.config import load_clause_map, load_drill, load_policy, load_tools, repo_root
from ksdrill.evidence.card import render_html

pytestmark = pytest.mark.unit


def test_configs_load_and_budget_is_ours() -> None:
    root = repo_root()
    drill = load_drill(root / "config" / "drill.yaml")
    tools = load_tools(root / "config" / "tools.yaml")
    policy = load_policy(root / "config" / "policy.yaml")
    assert drill.budgets_ms.total == 2000
    assert drill.bind_host == "127.0.0.1"
    assert {tool.id for tool in tools.tools} == {"files", "payments", "email"}
    assert policy.caps.payments.max_amount_eur == 5000
    assert policy.deny_by_default is True


def test_clause_map_is_fully_verified() -> None:
    clause_file = load_clause_map(repo_root() / "config" / "clause_map.yaml")
    assert len(clause_file.clauses) == 47
    assert clause_file.meta.tally == 47
    assert clause_file.meta.verified == 47
    assert clause_file.meta.unverified == 0
    assert all(clause.verified and clause.verified_on == "2026-11-12" for clause in clause_file.clauses)
    by_id = {clause.id: clause for clause in clause_file.clauses}
    assert "2 December 2027" in by_id["EU-14"].quote
    assert "2 August 2028" in by_id["EU-14"].quote
    assert by_id["EU-14"].quote_kind == "verbatim"
    assert "does not amend the wording" in by_id["EU-15"].quote
    assert by_id["EU-15"].quote_kind == "finding"
    assert "Article 2(13)" in (by_id["EU-15"].note or "")
    assert "not amended" in (by_id["EU-13"].note or "")
    assert "UNVERIFIED" not in (by_id["EU-13"].note or "")
    assert "UNVERIFIED" not in (by_id["EU-14"].note or "")
    assert "UNVERIFIED" not in (by_id["EU-15"].note or "")
    assert "(not read)" not in by_id["EU-15"].quote
    markdown = (repo_root() / "docs" / "CLAUSE-MAP.md").read_text(encoding="utf-8")
    for clause in clause_file.clauses:
        assert f"| {clause.id} |" in markdown
    assert "| UNVERIFIED |" not in markdown


def test_unverified_label_still_renders() -> None:
    html = render_html(_synthetic_model(), 8)
    assert "UNVERIFIED" in html
    assert "unverified" in html
    assert "EU-SYN" in html


def test_agent_package_does_not_import_harness() -> None:
    root = repo_root() / "src" / "ksdrill" / "agent"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "harness" not in node.module, path.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "harness" not in alias.name


def test_cli_help() -> None:
    from typer.testing import CliRunner

    from ksdrill.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "drill" in result.stdout


def _synthetic_model() -> dict:
    return {
        "run_id": "r-synthetic",
        "started": "2026-11-12T12:00:00+01:00",
        "operator": "joe",
        "trigger": "operator",
        "llm": "mock",
        "env": {"os": "test", "python": "3.12", "cpu": "test", "git_commit": "abc", "config_sha256": "ab" * 32},
        "deployment": {
            "purpose": "synthetic",
            "owners": {"stop_owner": "Joe"},
            "stop_mechanism": "ksdrill stop",
            "fallback": "manual",
            "log_locations": ["log"],
        },
        "preflight_ok": True,
        "ttl_s": 300,
        "secret_leak_count": 0,
        "admin_key_isolated": True,
        "allows_after_latch": 0,
        "llm_backend_down": None,
        "restore_line": "not run",
        "verdict": "PASS",
        "time_to_safe_state_ms": 10,
        "time_to_latch_ms": 1,
        "detection_time_ms": None,
        "budget_ms": 2000,
        "steps": [
            {"id": "S0", "name": "latch", "duration_ms": 1, "pass": True, "predicate": "latch_state", "clauses": ["HK-01"]}
        ],
        "bypasses": [],
        "log": {"verified": True, "head_hash": "ab" * 32, "records": 1},
        "excerpt": [],
        "clauses": [
            {
                "id": "EU-SYN",
                "verified": False,
                "page": None,
                "ref": "synthetic fixture",
                "quote": "not a real clause",
                "quote_kind": "finding",
                "source": "test",
                "note": None,
            }
        ],
        "verified_count": 0,
        "clause_count": 1,
        "verified_on": "2026-11-12",
        "retention_days": 183,
    }
