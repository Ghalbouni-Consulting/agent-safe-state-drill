# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Per-tool HMAC tokens with a short TTL and an epoch for revoke-all."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from jwt import InvalidTokenError

from ksdrill.clock import paris_iso
from ksdrill.models import PolicyFile, ToolSpec
from ksdrill.util import atomic_write, json_dumps


@dataclass
class IssuedToken:
    ok: bool
    status: int
    code: str
    token: str = ""
    jti: str = ""
    exp: int = 0
    scope: str = ""
    epoch: int = 0


@dataclass
class TokenVerdict:
    ok: bool
    code: str
    claims: dict[str, Any] | None = None
    jti: str | None = None


@dataclass
class RevokeReport:
    epoch: int
    revoked_jtis: list[str]
    issuing: bool


class TokenBroker:
    def __init__(
        self,
        *,
        signing_key: str,
        bootstrap_key: str,
        agent_id: str,
        state_path: Path,
        tools: dict[str, ToolSpec],
        policy: PolicyFile,
        default_ttl: int,
    ) -> None:
        self.signing_key = signing_key
        self.bootstrap_key = bootstrap_key
        self.agent_id = agent_id
        self.state_path = state_path
        self.tools = tools
        self.policy = policy
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self.epoch = 1
        self.issuing = True
        self.bootstrap_enabled = True
        self.denylist: set[str] = set()
        self._outstanding: dict[str, dict[str, Any]] = {}
        # Raw tokens stay in memory so a predicate can replay them. Never persisted.
        self._live: dict[str, str] = {}
        self._latch_set = False
        if state_path.exists():
            self._load()

    def set_latched(self, latched: bool) -> None:
        self._latch_set = latched

    def _load(self) -> None:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.epoch = int(data.get("epoch", 1))
        self.issuing = bool(data.get("issuing", True))
        self.bootstrap_enabled = bool(data.get("bootstrap_enabled", True))
        self.denylist = set(data.get("denylist") or [])
        for jti, meta in (data.get("outstanding") or {}).items():
            self._outstanding[jti] = meta

    def _persist(self) -> None:
        payload = {
            "epoch": self.epoch,
            "issuing": self.issuing,
            "bootstrap_enabled": self.bootstrap_enabled,
            "denylist": sorted(self.denylist),
            "outstanding": self._outstanding,
            "updated": paris_iso(),
        }
        atomic_write(self.state_path, json_dumps(payload) + "\n", fsync=True)

    def issue(self, agent_id: str, bootstrap_key: str, tool_id: str, action: str) -> IssuedToken:
        import hmac
        import time

        if not hmac.compare_digest(bootstrap_key, self.bootstrap_key):
            return IssuedToken(ok=False, status=401, code="AGENT_AUTH")
        with self._lock:
            if self._latch_set or not self.issuing or not self.bootstrap_enabled:
                return IssuedToken(ok=False, status=403, code="BROKER_LOCKED")
            if agent_id != self.agent_id:
                return IssuedToken(ok=False, status=403, code="SCOPE_NOT_ALLOWED")
            allowed = self.policy.allow.get(tool_id, [])
            if self.policy.deny_by_default and action not in allowed:
                return IssuedToken(ok=False, status=403, code="SCOPE_NOT_ALLOWED")
            spec = self.tools.get(tool_id)
            if spec is None or action not in spec.actions:
                return IssuedToken(ok=False, status=403, code="SCOPE_NOT_ALLOWED")
            ttl = spec.ttl_s or self.default_ttl
            now = int(time.time())
            jti = str(uuid.uuid4())
            scope = f"{tool_id}:{action}"
            claims = {
                "iss": "ksdrill-broker",
                "sub": f"agent:{agent_id}",
                "aud": tool_id,
                "scope": scope,
                "iat": now,
                "exp": now + ttl,
                "jti": jti,
                "epoch": self.epoch,
            }
            token = jwt.encode(claims, self.signing_key, algorithm="HS256")
            self._outstanding[jti] = {
                "tool": tool_id,
                "action": action,
                "scope": scope,
                "exp": claims["exp"],
                "epoch": self.epoch,
            }
            self._live[jti] = token
            self._persist()
            return IssuedToken(
                ok=True,
                status=200,
                code="ISSUED",
                token=token,
                jti=jti,
                exp=int(claims["exp"]),
                scope=scope,
                epoch=self.epoch,
            )

    def verify(self, token: str, tool_id: str, action: str) -> TokenVerdict:
        try:
            claims = jwt.decode(
                token,
                self.signing_key,
                algorithms=["HS256"],
                options={"verify_aud": False, "require": ["exp", "iat", "jti", "scope", "epoch"]},
            )
        except jwt.ExpiredSignatureError:
            jti = _unsafe_jti(token)
            return TokenVerdict(ok=False, code="TOKEN_EXPIRED", jti=jti)
        except InvalidTokenError:
            return TokenVerdict(ok=False, code="TOKEN_INVALID", jti=_unsafe_jti(token))
        jti = str(claims.get("jti"))
        scope = f"{tool_id}:{action}"
        if claims.get("aud") != tool_id or claims.get("scope") != scope:
            return TokenVerdict(ok=False, code="SCOPE_MISMATCH", claims=claims, jti=jti)
        if int(claims.get("epoch", -1)) != self.epoch or jti in self.denylist:
            return TokenVerdict(ok=False, code="TOKEN_REVOKED", claims=claims, jti=jti)
        return TokenVerdict(ok=True, code="OK", claims=claims, jti=jti)

    def revoke_all(self, reason: str) -> RevokeReport:
        del reason
        with self._lock:
            jtis = list(self._outstanding.keys())
            self.denylist.update(jtis)
            self.epoch += 1
            self.issuing = False
            self.bootstrap_enabled = False
            self._persist()
            return RevokeReport(epoch=self.epoch, revoked_jtis=jtis, issuing=self.issuing)

    def enable_for_restore(self) -> int:
        """New epoch so pre-stop tokens stay dead, and issuing is allowed again."""
        with self._lock:
            self.epoch += 1
            self.issuing = True
            self.bootstrap_enabled = True
            self._outstanding.clear()
            self._live.clear()
            self._persist()
            return self.epoch

    def outstanding(self) -> list[str]:
        with self._lock:
            return sorted(self._outstanding.keys())

    def live_tokens(self) -> list[tuple[str, str, str, str]]:
        """(jti, token, tool, action) for tokens still held in memory."""
        with self._lock:
            rows = []
            for jti, token in self._live.items():
                meta = self._outstanding.get(jti) or {}
                rows.append((jti, token, str(meta.get("tool", "")), str(meta.get("action", ""))))
            return rows


def _unsafe_jti(token: str) -> str | None:
    try:
        claims = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
            algorithms=["HS256"],
        )
    except InvalidTokenError:
        return None
    jti = claims.get("jti")
    return str(jti) if jti else None
