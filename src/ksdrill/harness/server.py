# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Process entrypoint. One process, two loopback ports, nothing else."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import typer
import uvicorn

from ksdrill.harness.app import create_admin_app, create_agent_app
from ksdrill.harness.context import HarnessContext
from ksdrill.util import atomic_write

app = typer.Typer(add_completion=False)


@app.command()
def serve(run_dir: Path) -> None:
    ctx = HarnessContext(run_dir)
    ctx.startup()
    _write_pid(run_dir / "state" / "harness.pid")
    agent_app = create_agent_app(ctx)
    admin_app = create_admin_app(ctx)
    host = ctx.settings.bind_host
    try:
        asyncio.run(_serve(ctx, agent_app, admin_app, host))
    finally:
        ctx.shutdown()


async def _serve(ctx: HarnessContext, agent_app: Any, admin_app: Any, host: str) -> None:
    server_agent = uvicorn.Server(
        uvicorn.Config(
            agent_app,
            host=host,
            port=ctx.settings.ports.agent,
            access_log=False,
            log_level="warning",
        )
    )
    server_admin = uvicorn.Server(
        uvicorn.Config(
            admin_app,
            host=host,
            port=ctx.settings.ports.admin,
            access_log=False,
            log_level="warning",
        )
    )
    server_agent.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    server_admin.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    def _stop(*_args: object) -> None:
        server_agent.should_exit = True
        server_admin.should_exit = True

    loop = asyncio.get_running_loop()
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop)
    await asyncio.gather(server_agent.serve(), server_admin.serve())


def _write_pid(path: Path) -> None:
    pgid = os.getpgrp() if os.name != "nt" else os.getpid()
    atomic_write(path, json.dumps({"pid": os.getpid(), "pgid": pgid}) + "\n", fsync=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
