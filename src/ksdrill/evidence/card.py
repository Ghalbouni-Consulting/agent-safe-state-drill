# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Build the one-page evidence card from a run directory. Clause text comes from the map."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ksdrill.config import config_sha256, load_clause_map, load_deployment, load_drill, repo_root
from ksdrill.evidence.pdf import PdfUnavailable, page_count, write_pdf
from ksdrill.harness.auditlog import verify_log_dir
from ksdrill.util import atomic_write, find_secret_leaks

DISCLAIMER = (
    "Synthetic demo agent and fake tools. Not an HKMA, ANSSI or EU assessment or "
    "certification. HKMA DP is non-binding (§2.3 p. 11). EU AI Act articles cited "
    "apply to high-risk AI systems. Timings measured on this machine."
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class CardError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.exit_code = 6


def build_card(run_dir: Path, *, write_pdf_file: bool | None = None, font_px: float | None = None) -> Path:
    """Render card.html. When a PDF is requested it must be exactly one A4 page."""
    if write_pdf_file is None:
        write_pdf_file = True
    model = card_model(run_dir)
    html_path = run_dir / "evidence" / "card.html"
    pdf_path = run_dir / "evidence" / "card.pdf"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    if not write_pdf_file:
        html_path.write_text(render_html(model, font_px or 7.0), encoding="utf-8")
        return html_path
    last_pages = None
    for size in (font_px,) if font_px else (7.2, 6.8, 6.4, 6.0, 5.6, 5.2):
        if size is None:
            continue
        html_path.write_text(render_html(model, float(size)), encoding="utf-8")
        try:
            write_pdf(html_path, pdf_path)
        except PdfUnavailable:
            print("warning: PDF skipped (Playwright/Chromium not available). HTML is the card.", flush=True)
            return html_path
        except Exception as exc:  # browser missing or failing to launch
            print(f"warning: PDF skipped ({exc.__class__.__name__}: {exc}). HTML is the card.", flush=True)
            return html_path
        last_pages = page_count(pdf_path)
        if last_pages == 1:
            return html_path
    raise CardError(f"evidence card PDF is {last_pages} pages, expected 1")


def render_html(model: dict[str, Any], font_px: float) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("card.html.j2")
    html = template.render(**model, font_px=font_px)
    if DISCLAIMER not in html:
        raise CardError("evidence card is missing the required disclaimer")
    return html


def card_model(run_dir: Path) -> dict[str, Any]:
    result = _load_result(run_dir)
    clause_file = load_clause_map(run_dir / "config" / "clause_map.yaml")
    by_id = {clause.id: clause for clause in clause_file.clauses}
    deployment = load_deployment(run_dir / "config" / "deployment_record.yaml")
    drill = load_drill(run_dir / "config" / "drill.yaml")
    referenced: list[str] = []
    steps = []
    for step in result.get("steps", []):
        clauses = list(step.get("clauses") or [])
        if not clauses:
            raise CardError(f"step {step.get('id')} has no clause id")
        referenced.extend(clauses)
        steps.append(
            {
                "id": step.get("id"),
                "name": step.get("name"),
                "duration_ms": step.get("duration_ms"),
                "pass": bool(step.get("pass")),
                "predicate": _predicate(step.get("predicates") or {}),
                "clauses": clauses,
            }
        )
    bypasses = []
    for row in result.get("bypasses", []):
        clauses = list(row.get("clauses") or [])
        if not clauses:
            raise CardError(f"bypass {row.get('id')} has no clause id")
        referenced.extend(clauses)
        bypasses.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "expected": row.get("expected"),
                "observed": _clip(str(row.get("observed", "")), 90),
                "pass": bool(row.get("pass")),
                "clauses": clauses,
            }
        )
    missing = sorted({item for item in referenced if item not in by_id})
    if missing:
        raise CardError("unknown clause ids: " + ", ".join(missing))
    clauses = []
    for clause in clause_file.clauses:
        clauses.append(
            {
                "id": clause.id,
                "verified": clause.verified,
                "page": clause.page,
                "ref": clause.ref,
                "quote": clause.quote,
                "quote_kind": clause.quote_kind,
                "source": clause.source,
                "note": clause.note,
            }
        )
    return {
        "run_id": result.get("run_id", run_dir.name),
        "started": result.get("started", ""),
        "operator": result.get("operator", ""),
        "trigger": result.get("trigger", ""),
        "llm": result.get("llm", ""),
        "env": result.get("env") or {},
        "deployment": deployment.model_dump(),
        "preflight_ok": result.get("preflight_ok", False),
        "ttl_s": drill.token_ttl_s,
        "secret_leak_count": len(result.get("secret_leaks") or []),
        "admin_key_isolated": result.get("admin_key_isolated", False),
        "allows_after_latch": result.get("allows_after_latch", ""),
        "llm_backend_down": result.get("llm_backend_down"),
        "restore_line": result.get("restore_line") or "not run",
        "verdict": result.get("verdict", "FAIL"),
        "time_to_safe_state_ms": result.get("time_to_safe_state_ms"),
        "time_to_latch_ms": result.get("time_to_latch_ms"),
        "detection_time_ms": result.get("detection_time_ms"),
        "budget_ms": result.get("budget_ms"),
        "steps": steps,
        "bypasses": bypasses,
        "log": result.get("log") or {"verified": False, "head_hash": "", "records": 0},
        "excerpt": _excerpt(run_dir),
        "clauses": clauses,
        "verified_count": sum(1 for clause in clause_file.clauses if clause.verified),
        "clause_count": len(clause_file.clauses),
        "verified_on": clause_file.meta.verified_on,
        "retention_days": drill.retention_days,
    }


def _load_result(run_dir: Path) -> dict[str, Any]:
    """Prefer a drill result. A live stop is enough; refresh those cards each time."""
    path = run_dir / "drill_result.json"
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("source") != "live":
            return stored
    if not (run_dir / "stop_report.json").exists():
        raise CardError(
            "No drill_result.json and no stop_report.json. "
            "Run ksdrill stop (live demo) or ksdrill drill before ksdrill card. "
            "The card is built from a stop that already happened; it does not invent one."
        )
    result = assemble_live_result(run_dir)
    atomic_write(path, json.dumps(result, indent=2) + "\n", fsync=True)
    return result


def assemble_live_result(run_dir: Path) -> dict[str, Any]:
    """Build a card payload from a live stop, plus bypass_result.json when present."""
    stop = json.loads((run_dir / "stop_report.json").read_text(encoding="utf-8"))
    bypasses = _load_bypasses(run_dir)
    digest = config_sha256(run_dir / "config")
    verified = verify_log_dir(run_dir / "log")
    leaks = find_secret_leaks(run_dir, _secret_values(run_dir))
    steps = list(stop.get("steps") or [])
    steps_ok = bool(steps) and all(bool(step.get("pass")) for step in steps)
    within = stop.get("within_budget")
    if within is None and stop.get("time_to_safe_state_ms") is not None:
        within = float(stop["time_to_safe_state_ms"]) <= float(stop.get("budget_ms") or 0)
    bypass_ok = all(bool(row.get("pass")) for row in bypasses) if bypasses else True
    verdict = "PASS" if steps_ok and within and bypass_ok and verified.ok and not leaks else "FAIL"
    llm_path = run_dir / "state" / "llm.json"
    llm = "mock"
    if llm_path.exists():
        llm = str(json.loads(llm_path.read_text(encoding="utf-8")).get("mode") or llm)
    return {
        "source": "live",
        "run_id": stop.get("run_id") or run_dir.name,
        "started": _first_ts(run_dir),
        "operator": stop.get("operator") or "",
        "trigger": stop.get("trigger") or "operator",
        "reason": stop.get("reason") or "",
        "llm": llm,
        "preflight_ok": None,
        "env": _machine(digest),
        "steps": steps,
        "time_to_safe_state_ms": stop.get("time_to_safe_state_ms"),
        "time_to_latch_ms": stop.get("time_to_latch_ms"),
        "detection_time_ms": stop.get("detection_time_ms"),
        "budget_ms": stop.get("budget_ms"),
        "within_budget": bool(within),
        "llm_backend_down": stop.get("llm_backend_down"),
        "bypasses": bypasses,
        "log": {
            "records": verified.records,
            "head_hash": verified.head_hash,
            "verified": verified.ok,
            "sealed": verified.ok,
            "manifest_sha256": "",
        },
        "allows_after_latch": _allows_after_latch(run_dir),
        "secret_leaks": leaks,
        "admin_key_isolated": None,
        "restore_line": "not run",
        "verdict": verdict,
    }


def _load_bypasses(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "bypass_result.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("bypasses") if isinstance(payload, dict) else payload
    return list(rows or [])


def _secret_values(run_dir: Path) -> list[str]:
    values: list[str] = []
    secrets = run_dir / "secrets"
    if not secrets.exists():
        return values
    for path in secrets.iterdir():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if path.name.endswith(".json"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                values.append(text)
                continue
            if isinstance(parsed, dict):
                values.extend(str(item) for item in parsed.values())
            continue
        values.append(text)
    return values


def _allows_after_latch(run_dir: Path) -> int:
    latch_seq = None
    records = _read_records(run_dir)
    for record in records:
        if record.get("event") == "latch_set":
            latch_seq = int(record["seq"])
    if latch_seq is None:
        return 0
    return sum(1 for record in records if int(record.get("seq", 0)) > latch_seq and record.get("decision") == "allow")


def _first_ts(run_dir: Path) -> str:
    records = _read_records(run_dir)
    if not records:
        return ""
    return str(records[0].get("ts", ""))


def _read_records(run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    log_dir = run_dir / "log"
    for name in ("audit-000.jsonl", "audit-001.jsonl"):
        path = log_dir / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _machine(digest: str) -> dict[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root(),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        commit = "unknown"
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or f"{os.cpu_count()} cores",
        "git_commit": commit,
        "config_sha256": digest,
    }


def render_clause_markdown(run_or_repo: Path) -> str:
    path = run_or_repo / "config" / "clause_map.yaml"
    if not path.exists():
        path = run_or_repo / "clause_map.yaml"
    clause_file = load_clause_map(path)
    lines = [
        "# Clause map",
        "",
        f"Verified {clause_file.meta.verified_on}. "
        f"{clause_file.meta.tally} rows, {clause_file.meta.verified} verified, "
        f"{clause_file.meta.unverified} unverified.",
        "",
        clause_file.meta.note or "",
        "",
        "| ID | Requirement | Source | Reference | Page | Verified | Quote |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for clause in clause_file.clauses:
        flag = "Y" if clause.verified else "UNVERIFIED"
        lines.append(
            "| "
            + " | ".join(
                [
                    clause.id,
                    _cell(clause.requirement),
                    _cell(clause.source),
                    _cell(clause.ref),
                    _cell(clause.page),
                    flag,
                    _cell(clause.quote),
                ]
            )
            + " |"
        )
        if clause.note:
            lines.append(f"|  | note |  |  |  |  | {_cell(clause.note)} |")
    lines.append("")
    return "\n".join(lines)


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _predicate(predicates: dict[str, Any]) -> str:
    parts = []
    for key, value in predicates.items():
        if value is True:
            parts.append(key)
        elif value is False:
            parts.append(f"{key}=FAIL")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts[:6])


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _excerpt(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in ("audit-000.jsonl", "audit-001.jsonl"):
        path = run_dir / "log" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    wanted = [
        lambda row: row.get("event") == "stop_received" and row.get("decision") != "already_safe",
        lambda row: row.get("event") == "latch_set",
        lambda row: row.get("event") == "step_result" and row.get("action") == "S2",
        lambda row: row.get("event") == "bypass_attempt",
        lambda row: row.get("event") == "log_sealed",
    ]
    picked = []
    for predicate in wanted:
        for row in records:
            if predicate(row):
                picked.append(row)
                break
    return picked
