# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Load and hash the YAML configuration that defines a run."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ksdrill.models import (
    ClauseMapFile,
    DeploymentRecord,
    DrillConfig,
    PolicyFile,
    Scenario,
    ToolsFile,
)
from ksdrill.util import json_dumps, sha256_text

CONFIG_FILES = (
    "drill.yaml",
    "tools.yaml",
    "policy.yaml",
    "deployment_record.yaml",
    "clause_map.yaml",
)


def repo_root() -> Path:
    env = os.environ.get("KSD_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "config").exists():
            return candidate
    raise RuntimeError("cannot locate the repository root (set KSD_REPO_ROOT)")


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ValueError(f"empty YAML: {path}")
    return data


def load_drill(path: Path) -> DrillConfig:
    return DrillConfig.model_validate(_load_yaml(path))


def load_tools(path: Path) -> ToolsFile:
    return ToolsFile.model_validate(_load_yaml(path))


def load_policy(path: Path) -> PolicyFile:
    return PolicyFile.model_validate(_load_yaml(path))


def load_deployment(path: Path) -> DeploymentRecord:
    return DeploymentRecord.model_validate(_load_yaml(path))


def load_clause_map(path: Path) -> ClauseMapFile:
    return ClauseMapFile.model_validate(_load_yaml(path))


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(_load_yaml(path))


def config_sha256(config_dir: Path) -> str:
    parts: list[str] = []
    for name in CONFIG_FILES:
        raw = (config_dir / name).read_bytes()
        parts.append(name)
        parts.append(raw.decode("utf-8"))
    return sha256_text(json_dumps(parts))


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
