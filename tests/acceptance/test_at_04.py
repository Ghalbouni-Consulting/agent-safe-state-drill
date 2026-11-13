# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import json

import pytest

from ksdrill.harness.auditlog import verify_file, verify_log_dir

pytestmark = pytest.mark.acceptance


def test_at_04_clean_chain_verifies_and_a_mutated_copy_names_the_seq(live) -> None:
    issued = live.issue("files", "read")
    live.call("files", "read", issued["body"]["token"], {"path": "INV-001.json"}, "at04")
    report = verify_log_dir(live.run / "log")
    assert report.ok
    source = (live.run / "log" / "audit-000.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(source[0])
    seq = int(record["seq"])
    record["hash"] = ("0" if record["hash"][0] != "0" else "1") + record["hash"][1:]
    source[0] = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    copy = live.run / "evidence" / "mutated.jsonl"
    copy.write_text("\n".join(source) + "\n", encoding="utf-8")
    broken = verify_file(copy)
    assert broken.summary == f"BROKEN at seq {seq}"
    assert verify_file(live.run / "log" / "audit-000.jsonl").ok
