# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

from ksdrill.runstore import read_secret
from ksdrill.util import find_secret_leaks
from tests.support import run_scenario

pytestmark = pytest.mark.acceptance


def test_at_05_no_secrets_outside_the_secrets_dir(tmp_path) -> None:
    live = run_scenario(tmp_path)
    try:
        secrets = [
            read_secret(live.run, "admin.key"),
            read_secret(live.run, "signing.key"),
            read_secret(live.run, "agent.key"),
        ]
        replay = live.run / "secrets" / "replay_tokens.json"
        if replay.exists():
            import json

            secrets.extend(json.loads(replay.read_text(encoding="utf-8")).values())
        leaks = find_secret_leaks(live.run, secrets)
        assert leaks == []
    finally:
        live.close()
