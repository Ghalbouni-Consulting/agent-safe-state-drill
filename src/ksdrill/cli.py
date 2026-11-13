# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""ksdrill command line. The library, this CLI, and the FastAPI apps are separate."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import typer

from ksdrill import __version__
from ksdrill.agent.launcher import launch_and_wait
from ksdrill.config import repo_root
from ksdrill.drill.runner import DrillOptions, run_drill
from ksdrill.evidence.card import CardError, build_card
from ksdrill.harness.auditlog import export_chain_text, verify_log_dir
from ksdrill.procutil import python_module, spawn, stop_process, wait_http
from ksdrill.runstore import init_run, read_secret, resolve_run, runs_root

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Agent Safe-State Drill")
harness_app = typer.Typer(help="Start or stop the control-plane process.")
agent_app = typer.Typer(help="Start the untrusted agent process.")
app.add_typer(harness_app, name="harness")
app.add_typer(agent_app, name="agent")


def main() -> None:
    app()


@app.callback()
def _root(ctx: typer.Context) -> None:
    ctx.obj = ctx.obj or {}


@app.command("init")
def init_cmd(
    runs: Path | None = typer.Option(None, "--runs-root", help="Directory that holds runs."),
) -> None:
    """Create a run directory, secrets and a config snapshot."""
    run = init_run(runs_root(runs), repo=repo_root())
    typer.echo(run.name)


@harness_app.command("up")
def harness_up(
    isolation: str = typer.Option("local", "--isolation", help="local, or docker (not in this build)."),
    probe_token_layer: bool = typer.Option(
        True,
        "--probe-token-layer/--no-probe-token-layer",
        help="B1/B2 token-layer probe. On by default. Still needs header X-Probe-Token-Layer: 1.",
    ),
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Start the harness on 127.0.0.1. Returns when both ports answer."""
    if isolation == "docker":
        typer.echo("docker isolation is not in this build. Use --isolation local.")
        raise typer.Exit(2)
    if isolation != "local":
        typer.echo(f"unknown isolation: {isolation}")
        raise typer.Exit(2)
    run_dir = resolve_run(runs_root(runs), run)
    _set_probe(run_dir, probe_token_layer)
    proc = _spawn_harness(run_dir)
    drill = _ports(run_dir)
    agent_url = f"http://{drill['bind_host']}:{drill['ports']['agent']}/health"
    admin_url = f"http://{drill['bind_host']}:{drill['ports']['admin']}/health"
    try:
        wait_http(agent_url)
        wait_http(admin_url)
    except TimeoutError:
        stop_process(proc)
        typer.echo("harness did not become healthy (port busy or failed to start)")
        raise typer.Exit(2) from None
    if proc.poll() is not None:
        typer.echo("harness exited during startup")
        raise typer.Exit(2)
    probe = "on" if probe_token_layer else "off"
    typer.echo(
        f"harness up {run_dir.name} agent={drill['ports']['agent']} admin={drill['ports']['admin']} probe={probe}"
    )


@harness_app.command("down")
def harness_down(
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Stop the harness process recorded for this run."""
    run_dir = resolve_run(runs_root(runs), run)
    _signal_pidfile(run_dir / "state" / "harness.pid")
    typer.echo("harness down")


@agent_app.command("start")
def agent_start(
    scenario: str = typer.Option("supplier_payments", "--scenario"),
    llm: str = typer.Option("mock", "--llm", help="mock (default) or ollama."),
    pause_at_step: int | None = typer.Option(None, "--pause-at-step", help="Sleep after this step so an operator can stop."),
    email_delay_ms: int | None = typer.Option(
        None,
        "--email-delay-ms",
        help="How long a queued email stays in flight before dispatch. Overrides the run config and KSD_EMAIL_DELAY_MS.",
    ),
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Launch the agent. Exit 3 if the safe-state latch is already set."""
    run_dir = resolve_run(runs_root(runs), run)
    delay = _resolve_email_delay(email_delay_ms)
    if delay is not None:
        _apply_email_delay(run_dir, delay)
    name = scenario if scenario.endswith(".yaml") else f"{scenario}.yaml"
    code = launch_and_wait(
        run_dir,
        repo=repo_root(),
        llm=llm,
        scenario=name,
        pause_at_step=pause_at_step,
    )
    raise typer.Exit(code)


@app.command("status")
def status_cmd(
    watch: bool = typer.Option(False, "--watch"),
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Show latch, tools, queue and the current step. Tokens are printed as jti only."""
    run_dir = resolve_run(runs_root(runs), run)
    while True:
        payload = _admin_get(run_dir, "/admin/status")
        _print_status(payload)
        if not watch:
            return
        time.sleep(0.5)


@app.command("stop")
def stop_cmd(
    operator: str = typer.Option("joe", "--operator"),
    reason: str = typer.Option(..., "--reason"),
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Run the latch and the six safe-state steps, and print each duration."""
    run_dir = resolve_run(runs_root(runs), run)
    report = _admin_post(run_dir, "/admin/stop", {"operator": operator, "reason": reason, "trigger": "operator"})
    _print_steps(report)
    raise typer.Exit(0 if report.get("pass") or report.get("already_safe") else 4)


@app.command("drill")
def drill_cmd(
    llm: str = typer.Option("mock", "--llm"),
    stop_at_step: int = typer.Option(3, "--stop-at-step"),
    budget_ms: int = typer.Option(2000, "--budget-ms"),
    no_pdf: bool = typer.Option(False, "--no-pdf"),
    operator: str = typer.Option("joe", "--operator"),
    reason: str = typer.Option("drill", "--reason"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Start mid-task, stop, run bypasses B1–B9, and write the evidence card."""
    outcome = run_drill(
        DrillOptions(
            runs_root=runs_root(runs),
            repo=repo_root(),
            llm=llm,
            stop_at_step=stop_at_step,
            budget_ms=budget_ms,
            no_pdf=no_pdf,
            operator=operator,
            reason=reason,
            step_delay_ms=0 if llm == "mock" else None,
            stop_window_ms=5000,
            dispatcher_interval_ms=40 if llm == "mock" else None,
        )
    )
    typer.echo(f"{outcome.result['verdict']} {outcome.run_dir}")
    typer.echo(f"time_to_safe_state_ms={outcome.result.get('time_to_safe_state_ms')} (demo budget)")
    raise typer.Exit(outcome.exit_code)


@app.command("bypass")
def bypass_cmd(
    run: str | None = typer.Argument(None),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Re-run B1–B9 against an already stopped harness."""
    run_dir = resolve_run(runs_root(runs), run)

    drill = _ports(run_dir)
    host = drill["bind_host"]
    agent_url = f"http://{host}:{drill['ports']['agent']}"
    admin_url = f"http://{host}:{drill['ports']['admin']}"
    # The harness is already up. A dummy Popen is not available; B7 restarts via pid.
    raise typer.Exit(_bypass_attached(run_dir, agent_url, admin_url))


@app.command("verify-log")
def verify_log_cmd(
    run: str = typer.Argument(...),
    chain: str | None = typer.Option(None, "--chain", help="Print request → authorisation → action → result."),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Verify the hash chain. Exit 5 when it is broken."""
    run_dir = resolve_run(runs_root(runs), run)
    if chain:
        typer.echo(export_chain_text(run_dir / "log", chain))
    report = verify_log_dir(run_dir / "log")
    typer.echo(report.summary)
    raise typer.Exit(0 if report.ok else 5)


@app.command("card")
def card_cmd(
    run: str = typer.Argument(...),
    pdf: bool = typer.Option(True, "--pdf/--no-pdf"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Rebuild the evidence card. Exit 6 when the PDF is not a single page."""
    run_dir = resolve_run(runs_root(runs), run)
    try:
        path = build_card(run_dir, write_pdf_file=pdf)
    except CardError as exc:
        typer.echo(str(exc))
        raise typer.Exit(exc.exit_code) from exc
    typer.echo(path)


@app.command("restore")
def restore_cmd(
    operator: str = typer.Option("joe", "--operator"),
    approve: str = typer.Option(..., "--approve"),
    run: str | None = typer.Option(None, "--run"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Admin restore: new epoch, tools reconnect, executed actions are not replayed."""
    run_dir = resolve_run(runs_root(runs), run)
    try:
        payload = _admin_post(run_dir, "/admin/restore", {"operator": operator, "approve": approve})
    except httpx.HTTPStatusError as exc:
        typer.echo(exc.response.text)
        raise typer.Exit(4) from exc
    typer.echo(json.dumps(payload))


@app.command("reset")
def reset_cmd(
    yes: bool = typer.Option(False, "--yes"),
    runs: Path | None = typer.Option(None, "--runs-root"),
) -> None:
    """Delete runs/. Refuses without --yes."""
    if not yes:
        typer.echo("refusing to wipe runs/ without --yes")
        raise typer.Exit(2)
    root = runs_root(runs)
    if root.exists():
        import shutil

        shutil.rmtree(root)
    typer.echo("reset")


@app.command("version")
def version_cmd() -> None:
    typer.echo(__version__)


def _print_status(payload: dict[str, object]) -> None:
    task = payload.get("task") or {}
    assert isinstance(task, dict)
    jtis = payload.get("outstanding_jti") or []
    queue = payload.get("queue") or {}
    typer.echo(
        f"state={payload.get('state')} step={task.get('step')} phase={task.get('phase')} "
        f"task={task.get('status')} epoch={payload.get('epoch')} issuing={payload.get('issuing')}"
    )
    typer.echo(f"tools={payload.get('tools')} queue={queue}")
    typer.echo(f"jti={','.join(jtis) if isinstance(jtis, list) else jtis}")


def _print_steps(report: dict[str, object]) -> None:
    steps = report.get("steps") or []
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            mark = "PASS" if step.get("pass") else "FAIL"
            typer.echo(f"{step.get('id'):<4} {step.get('name', ''):<24} {step.get('duration_ms')} ms  {mark}")
    typer.echo(
        f"time to safe state: {report.get('time_to_safe_state_ms')} ms "
        f"(demo budget {report.get('budget_ms')} ms, our target, not a regulatory number)"
    )
    if report.get("already_safe"):
        typer.echo("already_safe=true")


def _ports(run_dir: Path) -> dict[str, object]:
    import yaml

    return yaml.safe_load((run_dir / "config" / "drill.yaml").read_text(encoding="utf-8"))


def _set_probe(run_dir: Path, enabled: bool) -> None:
    _set_drill_value(run_dir, "probe_token_layer", bool(enabled))


def _resolve_email_delay(flag: int | None) -> int | None:
    if flag is not None:
        return flag
    raw = os.environ.get("KSD_EMAIL_DELAY_MS")
    if raw is None or raw == "":
        return None
    return int(raw)


def _apply_email_delay(run_dir: Path, delay_ms: int) -> None:
    """Write the delay into the run config and, if the harness is up, into the process."""
    if delay_ms < 0:
        typer.echo("email delay must be >= 0")
        raise typer.Exit(2)
    _set_drill_value(run_dir, "email_dispatch_delay_ms", int(delay_ms))
    try:
        _admin_post(run_dir, "/admin/outbound/email-delay", {"delay_ms": int(delay_ms)})
    except httpx.ConnectError:
        typer.echo(f"email delay {delay_ms} ms saved for the next harness start")


def _set_drill_value(run_dir: Path, key: str, value: object) -> None:
    import yaml

    path = run_dir / "config" / "drill.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _spawn_harness(run_dir: Path):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["KSD_REPO_ROOT"] = str(repo_root())
    return spawn(
        python_module("ksdrill.harness.server", str(run_dir)),
        cwd=repo_root(),
        env=env,
        log_path=run_dir / "agent" / "harness.log",
    )


def _signal_pidfile(path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    pid = int(data["pid"])
    try:
        os.kill(pid, 15)
    except OSError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, 9)
    except OSError:
        return


def _admin_get(run_dir: Path, path: str) -> dict[str, object]:
    drill = _ports(run_dir)
    url = f"http://{drill['bind_host']}:{drill['ports']['admin']}{path}"
    response = httpx.get(url, headers={"X-Admin-Key": read_secret(run_dir, "admin.key")}, timeout=10.0)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {"value": body}


def _admin_post(run_dir: Path, path: str, payload: dict[str, object]) -> dict[str, object]:
    drill = _ports(run_dir)
    url = f"http://{drill['bind_host']}:{drill['ports']['admin']}{path}"
    response = httpx.post(
        url,
        headers={"X-Admin-Key": read_secret(run_dir, "admin.key")},
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {"value": body}


class _PidHarness:
    """Enough of a process handle for B7 to stop a harness we did not spawn."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.args: list[str] = []

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            os.kill(self.pid, 0)
        except OSError:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        try:
            os.kill(self.pid, 15)
        except OSError:
            self.returncode = 0

    def kill(self) -> None:
        try:
            os.kill(self.pid, 9)
        except OSError:
            self.returncode = 0

    def wait(self, timeout: float = 5.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.poll() is not None:
                return int(self.returncode or 0)
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(cmd="harness", timeout=timeout)


def _bypass_attached(run_dir: Path, agent_url: str, admin_url: str) -> int:
    """Bypass suite against a harness the operator already started."""
    from ksdrill.drill.bypass import run_bypasses

    admin_key = read_secret(run_dir, "admin.key")
    agent_key = read_secret(run_dir, "agent.key")
    tokens = _replay_tokens(run_dir)
    pid = json.loads((run_dir / "state" / "harness.pid").read_text(encoding="utf-8"))["pid"]
    proc = _PidHarness(int(pid))
    try:
        results, restarted = run_bypasses(
            run_dir=run_dir,
            repo=repo_root(),
            agent_url=agent_url,
            admin_url=admin_url,
            admin_key=admin_key,
            agent_key=agent_key,
            payment_token=tokens.get("payments", "not-a-token"),
            email_token=tokens.get("email", "not-a-token"),
            harness=proc,  # type: ignore[arg-type]
            probe=bool(_ports(run_dir).get("probe_token_layer")),
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
    # B7 replaces the handle with the restarted server. Stop that one too.
    if restarted is not proc:
        stop_process(restarted)
    failed = [item.id for item in results if not item.passed]
    payload = {"bypasses": [item.as_dict() for item in results]}
    (run_dir / "bypass_result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for item in results:
        mark = "PASS" if item.passed else "FAIL"
        typer.echo(f"{item.id} {mark} {item.observed}")
    return 0 if not failed else 4


def _replay_tokens(run_dir: Path) -> dict[str, str]:
    path = run_dir / "secrets" / "replay_tokens.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in loaded.items()}
