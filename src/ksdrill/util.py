# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Small file, hash and secret helpers shared by the harness."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def json_dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def args_digest(args: dict[str, Any]) -> str:
    return "sha256:" + sha256_text(json_dumps(args))


def atomic_write(path: Path, text: str, *, mode: int = 0o644, fsync: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, mode)
    try:
        os.write(fd, text.encode("utf-8"))
        if fsync:
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    if fsync:
        fsync_dir(path.parent)


def fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, value.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def set_readonly(path: Path) -> None:
    if os.name == "nt":
        os.chmod(path, stat.S_IREAD)
    else:
        os.chmod(path, 0o444)


def clear_readonly(path: Path) -> None:
    if os.name == "nt":
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    else:
        os.chmod(path, 0o644)


def is_readonly(path: Path) -> bool:
    mode = path.stat().st_mode
    writable = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    return (mode & writable) == 0


def assert_loopback(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"refusing non-loopback URL: {url}")


def assert_loopback_host(host: str) -> None:
    if host.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"refusing non-loopback bind host: {host}")


JWT_RE_SRC = r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"


def find_secret_leaks(root: Path, secrets: list[str]) -> list[str]:
    """Return relative paths under root, outside secrets/, that contain a secret or a JWT."""
    import re

    jwt_re = re.compile(JWT_RE_SRC)
    needles = [s for s in secrets if s and len(s) >= 8]
    leaks: list[str] = []
    if not root.exists():
        return leaks
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("secrets/"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in needles) or jwt_re.search(text):
            leaks.append(rel)
    return leaks
