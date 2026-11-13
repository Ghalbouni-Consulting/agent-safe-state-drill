# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU

import pytest

pytestmark = pytest.mark.acceptance


def test_at_03_default_deny_and_path_traversal(live) -> None:
    issued = live.issue("files", "read")
    token = issued["body"]["token"]
    unknown = live.call("shell", "exec", token, {"cmd": "id"}, "at03-unknown")
    assert unknown["status"] == 404
    assert unknown["body"]["detail"] == "DENY_UNKNOWN"
    unknown_action = live.call("files", "delete", token, {"path": "INV-001.json"}, "at03-action")
    assert unknown_action["status"] == 404
    traversal = live.call("files", "read", token, {"path": "../secrets/admin.key"}, "at03-dotdot")
    assert traversal["status"] == 403
    assert traversal["body"]["detail"] == "PATH_TRAVERSAL"
