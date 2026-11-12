# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Monotonic durations and Paris civil timestamps."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def monotonic_ns() -> int:
    return time.monotonic_ns()


def paris_now() -> datetime:
    return datetime.now(PARIS)


def paris_iso() -> str:
    return paris_now().isoformat(timespec="microseconds")


def make_run_id(when: datetime | None = None) -> str:
    stamp = (when or paris_now()).strftime("%Y%m%d-%H%M%S")
    return f"r-{stamp}"
