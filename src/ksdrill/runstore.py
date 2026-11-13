# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Create a self-contained run directory with secrets and a config snapshot."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from ksdrill.clock import make_run_id
from ksdrill.config import CONFIG_FILES, dump_yaml, load_drill, repo_root
from ksdrill.util import write_secret


def runs_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("KSD_RUNS_ROOT")
    if env:
        return Path(env)
    return repo_root() / "runs"


def init_run(
    runs: Path,
    *,
    repo: Path | None = None,
    overrides: dict[str, Any] | None = None,
    scenario_name: str = "supplier_payments.yaml",
) -> Path:
    root = repo or repo_root()
    runs.mkdir(parents=True, exist_ok=True)
    run_id = _unique_id(runs)
    run = runs / run_id
    for sub in (
        "secrets",
        "config",
        "state",
        "log",
        "sinks",
        "fallback",
        "evidence",
        "agent",
        "sandbox",
        "scenarios",
    ):
        (run / sub).mkdir(parents=True)
    try:
        os.chmod(run / "secrets", 0o700)
    except OSError:
        pass
    write_secret(run / "secrets" / "admin.key", secrets.token_urlsafe(32))
    write_secret(run / "secrets" / "signing.key", secrets.token_urlsafe(32))
    write_secret(run / "secrets" / "agent.key", secrets.token_urlsafe(32))

    for name in CONFIG_FILES:
        shutil.copyfile(root / "config" / name, run / "config" / name)
    if overrides:
        _apply_drill_overrides(run / "config" / "drill.yaml", overrides)
    scenario_src = root / "scenarios" / scenario_name
    shutil.copyfile(scenario_src, run / "scenarios" / scenario_name)
    sandbox = root / "sandbox"
    if sandbox.exists():
        for item in sandbox.iterdir():
            if item.is_file():
                shutil.copyfile(item, run / "sandbox" / item.name)
    (runs / "CURRENT").write_text(run_id + "\n", encoding="utf-8")
    (run / "RUN_ID").write_text(run_id + "\n", encoding="utf-8")
    return run


def _unique_id(runs: Path) -> str:
    base = make_run_id()
    if not (runs / base).exists():
        return base
    n = 2
    while (runs / f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"


def _apply_drill_overrides(path: Path, overrides: dict[str, Any]) -> None:
    cfg = load_drill(path)
    data = cfg.model_dump()
    for key, value in overrides.items():
        if key == "ports" and isinstance(value, dict):
            data["ports"].update(value)
        elif key == "budgets_ms" and isinstance(value, dict):
            data["budgets_ms"].update(value)
        elif key == "velocity" and isinstance(value, dict):
            data["velocity"].update(value)
        elif key == "ollama" and isinstance(value, dict):
            data["ollama"].update(value)
        else:
            data[key] = value
    # Re-validate before writing so a bad override fails at init.
    load_drill_from_dict = type(cfg).model_validate(data)
    path.write_text(dump_yaml(load_drill_from_dict.model_dump()), encoding="utf-8")


def resolve_run(runs: Path, run_id: str | None) -> Path:
    if run_id:
        path = runs / run_id
    else:
        pointer = runs / "CURRENT"
        if not pointer.exists():
            raise FileNotFoundError("no run yet; start with `ksdrill init`")
        path = runs / pointer.read_text(encoding="utf-8").strip()
    if not path.is_dir():
        raise FileNotFoundError(f"run not found: {path}")
    return path


def read_secret(run: Path, name: str) -> str:
    return (run / "secrets" / name).read_text(encoding="utf-8").strip()
