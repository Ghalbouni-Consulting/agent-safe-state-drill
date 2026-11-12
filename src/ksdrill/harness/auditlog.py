# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Append-only JSONL audit log with a SHA-256 hash chain."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksdrill.clock import monotonic_ns, paris_iso
from ksdrill.util import json_dumps

GENESIS = "0" * 64


def chain_hash(prev_hash: str, record: dict[str, Any]) -> str:
    """Hash prev_hash concatenated with the canonical JSON of record, excluding `hash`."""
    body = {key: value for key, value in record.items() if key != "hash"}
    payload = json_dumps(body)
    import hashlib

    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


@dataclass
class VerifyReport:
    ok: bool
    records: int
    head_hash: str
    broken_seq: int | None = None
    message: str = "OK"

    @property
    def summary(self) -> str:
        if self.ok:
            return f"OK records={self.records} head={self.head_hash}"
        return f"BROKEN at seq {self.broken_seq}"


class AuditLog:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = run_dir
        self.log_dir = run_dir / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = threading.RLock()
        self.forbidden: set[str] = set()
        self.seq = 0
        self.head = GENESIS
        self._sealed = False
        self._fh: Any = None
        self._path: Path | None = None
        self._load_existing()

    def ban(self, secret: str) -> None:
        if secret and len(secret) >= 8:
            self.forbidden.add(secret)

    def _load_existing(self) -> None:
        files = self.segment_paths()
        if not files:
            self._open_segment(self.log_dir / "audit-000.jsonl")
            return
        last_hash = GENESIS
        last_seq = 0
        sealed = False
        for path in files:
            for record in _read_jsonl(path):
                last_seq = int(record["seq"])
                last_hash = str(record["hash"])
                if record.get("event") == "log_sealed":
                    sealed = True
        self.seq = last_seq
        self.head = last_hash
        self._sealed = sealed
        if sealed:
            self._open_segment(self.log_dir / "audit-001.jsonl")
        else:
            self._open_segment(files[-1])

    def segment_paths(self) -> list[Path]:
        names = ("audit-000.jsonl", "audit-001.jsonl", "audit-002.jsonl")
        return [self.log_dir / name for name in names if (self.log_dir / name).exists()]

    def _open_segment(self, path: Path) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._path = path
        self._fh = path.open("a", encoding="utf-8")

    def append(self, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            blob = json_dumps(fields)
            for secret in self.forbidden:
                if secret in blob:
                    raise RuntimeError("refusing to write a secret into the audit log")
            seq = self.seq + 1
            record: dict[str, Any] = {
                "seq": seq,
                "ts": paris_iso(),
                "mono_ns": monotonic_ns(),
                "run_id": self.run_id,
                "actor": fields.get("actor"),
                "event": fields["event"],
                "tool": fields.get("tool"),
                "action": fields.get("action"),
                "request_id": fields.get("request_id"),
                "instruction_ref": fields.get("instruction_ref"),
                "token_jti": fields.get("token_jti"),
                "scope": fields.get("scope"),
                "decision": fields.get("decision"),
                "reason": fields.get("reason"),
                "args_digest": fields.get("args_digest"),
                "result": fields.get("result"),
                "approval": fields.get("approval"),
                "prev_hash": self.head,
            }
            record["hash"] = chain_hash(self.head, record)
            line = json_dumps(record)
            for secret in self.forbidden:
                if secret in line:
                    raise RuntimeError("refusing to write a secret into the audit log")
            assert self._fh is not None
            self._fh.write(line + "\n")
            self._fh.flush()
            try:
                os_fsync(self._fh.fileno())
            except OSError:
                pass
            self.head = str(record["hash"])
            self.seq = seq
            return record

    def read_all(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self.segment_paths():
            records.extend(_read_jsonl(path))
        return records

    def export_chain(self, request_id: str) -> list[dict[str, Any]]:
        return [row for row in self.read_all() if row.get("request_id") == request_id]

    def seal(self) -> dict[str, Any]:
        record = self.append(
            {
                "actor": "harness",
                "event": "log_sealed",
                "decision": "seal",
                "reason": "stop_preserve_logs",
                "result": f"head={self.head}",
            }
        )
        with self._lock:
            if self._fh is not None:
                self._fh.flush()
                os_fsync(self._fh.fileno())
                self._fh.close()
                self._fh = None
            self._sealed = True
            sealed_path = self.log_dir / "audit-000.jsonl"
            from ksdrill.util import set_readonly

            set_readonly(sealed_path)
            # Further events continue the chain in a new segment.
            self._open_segment(self.log_dir / "audit-001.jsonl")
        return record

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def os_fsync(fd: int) -> None:
    import os

    os.fsync(fd)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def verify_file(path: Path) -> VerifyReport:
    return verify_paths([path])


def export_chain_text(log_dir: Path, request_id: str) -> str:
    """Print the records that rebuild request → authorisation → action → result."""
    records: list[dict[str, Any]] = []
    for name in ("audit-000.jsonl", "audit-001.jsonl", "audit-002.jsonl"):
        path = log_dir / name
        if path.exists():
            records.extend(_read_jsonl(path))
    matched = [row for row in records if row.get("request_id") == request_id]
    labels = {
        "agent_step": "request",
        "token_issued": "authorisation",
        "tool_call": "action",
        "outbound_dispatched": "result",
    }
    lines = ["request → authorisation → action → result"]
    if not matched:
        lines.append(f"(no records for {request_id})")
        return "\n".join(lines)
    for row in matched:
        label = labels.get(str(row.get("event")), str(row.get("event")))
        lines.append(
            f"{label}: seq={row.get('seq')} event={row.get('event')} "
            f"decision={row.get('decision')} result={row.get('result')}"
        )
    return "\n".join(lines)


def verify_log_dir(log_dir: Path) -> VerifyReport:
    paths = [
        log_dir / name
        for name in ("audit-000.jsonl", "audit-001.jsonl", "audit-002.jsonl")
        if (log_dir / name).exists() and (log_dir / name).stat().st_size > 0
    ]
    if not paths:
        return VerifyReport(ok=True, records=0, head_hash=GENESIS, message="EMPTY")
    return verify_paths(paths)


def verify_paths(paths: list[Path]) -> VerifyReport:
    prev = GENESIS
    count = 0
    for path in paths:
        for record in _read_jsonl(path):
            count += 1
            seq = int(record.get("seq", -1))
            if record.get("prev_hash") != prev:
                return VerifyReport(
                    ok=False,
                    records=count,
                    head_hash=prev,
                    broken_seq=seq,
                    message=f"BROKEN at seq {seq}",
                )
            expected = chain_hash(str(record["prev_hash"]), record)
            if expected != record.get("hash"):
                return VerifyReport(
                    ok=False,
                    records=count,
                    head_hash=prev,
                    broken_seq=seq,
                    message=f"BROKEN at seq {seq}",
                )
            prev = str(record["hash"])
    return VerifyReport(ok=True, records=count, head_hash=prev, message="OK")
