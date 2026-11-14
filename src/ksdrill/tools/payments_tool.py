# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Example ledger. Idempotency keys make a restore unable to replay a payment."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from ksdrill.clock import paris_iso
from ksdrill.tools.base import ToolAdapter, ToolError


class PaymentsTool(ToolAdapter):
    tool_id = "payments"
    actions = ("initiate",)
    outbound = True
    channel = "bank"

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE,
                invoice_id TEXT,
                amount_eur REAL,
                payee TEXT,
                created_ts TEXT
            )
            """
        )
        conn.commit()
        self._conn = conn
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def apply(self, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        if not self.connected or self._conn is None:
            raise ToolError(503, "TOOLS_DISCONNECTED")
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM payments WHERE idempotency_key = ?",
                (idem_key,),
            ).fetchone()
            if row:
                return {"replayed": True, "ledger_row": int(row[0]), "invoice_id": args.get("invoice_id")}
            cur = self._conn.execute(
                """
                INSERT INTO payments (idempotency_key, invoice_id, amount_eur, payee, created_ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    idem_key,
                    str(args.get("invoice_id", "")),
                    float(args.get("amount_eur", 0)),
                    str(args.get("payee", "")),
                    paris_iso(),
                ),
            )
            self._conn.commit()
            return {
                "replayed": False,
                "ledger_row": int(cur.lastrowid or 0),
                "invoice_id": args.get("invoice_id"),
            }

    def count(self) -> int:
        if self._conn is None:
            if not self.db_path.exists():
                return 0
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute("SELECT COUNT(*) FROM payments").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error:
                return 0
            finally:
                conn.close()
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute("SELECT COUNT(*) FROM payments").fetchone()
            return int(row[0]) if row else 0

    def idempotency_keys(self) -> list[str]:
        if not self.db_path.exists():
            return []
        conn = self._conn or sqlite3.connect(self.db_path)
        owned = self._conn is None
        try:
            rows = conn.execute("SELECT idempotency_key FROM payments").fetchall()
            return [str(r[0]) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            if owned:
                conn.close()

    def call(self, action: str, args: dict[str, Any], idem_key: str) -> dict[str, Any]:
        if action != "initiate":
            raise ToolError(404, "DENY_UNKNOWN")
        return self.apply(args, idem_key)
