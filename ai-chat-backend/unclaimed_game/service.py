"""Local deterministic game, gated before discovery, execution or storage.

No request may supply its own identity or database path. The production default
has no identity verifier and is closed. Tests supply their own synthetic verifier
and temporary storage; this is not a production identity integration.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any


@dataclass(frozen=True)
class VerifiedPrincipal:
    account_id: str
    agent_id: str
    roles: frozenset[str]
    origin_agent_id: str


@dataclass(frozen=True)
class AccessPolicy:
    enabled: bool = False
    allowed: frozenset[tuple[str, str]] = frozenset()


class GameDenied(RuntimeError):
    def __init__(self):
        super().__init__("game_unavailable")


class GameFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_REVISION = {"type": "integer", "minimum": 0}
_TOOLS = (
    {"name": "get_game_state", "title": "读取当前游戏", "inputSchema": {
        "type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "choose_game_action", "title": "作出游戏选择", "inputSchema": {
        "type": "object", "properties": {"choice_id": {"type": "string"}, "revision": _REVISION},
        "required": ["choice_id", "revision"], "additionalProperties": False}},
    {"name": "restart_game", "title": "归档并重新开始", "inputSchema": {
        "type": "object", "properties": {"revision": _REVISION, "confirmation": {"const": True}},
        "required": ["revision", "confirmation"], "additionalProperties": False}},
    {"name": "start_next_story", "title": "进入续篇", "inputSchema": {
        "type": "object", "properties": {"revision": _REVISION, "confirmation": {"const": True},
                                          "route": {"enum": ["continue", "stay", "move", "mobile"]}},
        "required": ["revision", "confirmation", "route"], "additionalProperties": False}},
)


def validate_arguments(name: str, arguments: Any) -> None:
    spec = next((t["inputSchema"] for t in _TOOLS if t["name"] == name), None)
    if spec is None or type(arguments) is not dict:
        raise GameFailure("invalid_request")
    if set(arguments) != set(spec.get("required", [])):
        raise GameFailure("invalid_request")
    if name != "get_game_state":
        revision = arguments.get("revision")
        if type(revision) is not int or not 0 <= revision <= 9007199254740991:
            raise GameFailure("invalid_request")
    if name == "choose_game_action":
        choice = arguments["choice_id"]
        if not isinstance(choice, str) or not choice or len(choice) > 256:
            raise GameFailure("invalid_request")
    if name in {"restart_game", "start_next_story"} and arguments["confirmation"] is not True:
        raise GameFailure("confirmation_required")
    if name == "start_next_story" and (not isinstance(arguments["route"], str)
                                        or arguments["route"] not in {"continue", "stay", "move", "mobile"}):
        raise GameFailure("invalid_request")


class UnclaimedGameService:
    def __init__(self, *, node_executable: Path | None = None, storage_root: Path | None = None,
                 identity_verifier: Callable[[Any], VerifiedPrincipal | None] | None = None,
                 policy_provider: Callable[[], AccessPolicy] | None = None):
        self.node_executable = node_executable
        self.storage_root = storage_root
        self._verify = identity_verifier
        self._policy = policy_provider or AccessPolicy

    def _admit(self, auth_context: Any) -> VerifiedPrincipal:
        try:
            policy = self._policy()
            if (type(policy) is not AccessPolicy or policy.enabled is not True or self._verify is None
                    or type(policy.allowed) is not frozenset
                    or any(type(pair) is not tuple or len(pair) != 2
                           or any(type(value) is not str or not value for value in pair)
                           for pair in policy.allowed)):
                raise GameDenied()
            principal = self._verify(auth_context)
            if (type(principal) is not VerifiedPrincipal
                    or type(principal.account_id) is not str or not principal.account_id
                    or type(principal.agent_id) is not str or not principal.agent_id
                    or type(principal.origin_agent_id) is not str
                    or type(principal.roles) is not frozenset
                    or any(type(role) is not str for role in principal.roles)
                    or principal.origin_agent_id != principal.agent_id
                    or "unclaimed_test" not in principal.roles
                    or (principal.account_id, principal.agent_id) not in policy.allowed):
                raise GameDenied()
            return principal
        except Exception:
            raise GameDenied() from None

    def list_tools(self, auth_context: Any = None) -> list[dict[str, Any]]:
        try:
            self._admit(auth_context)
        except GameDenied:
            return []
        return copy.deepcopy(list(_TOOLS))

    async def call_tool(self, name: str, arguments: Any, *, auth_context: Any = None) -> dict[str, Any]:
        principal = self._admit(auth_context)
        validate_arguments(name, arguments)
        if self.node_executable is None or self.storage_root is None:
            raise GameFailure("local_runtime_unconfigured")
        namespace = hashlib.sha256(json.dumps([principal.account_id, principal.agent_id],
                                             separators=(",", ":")).encode()).hexdigest()
        # Identity and paths are chosen here from trusted process configuration,
        # never from tool arguments or a client-supplied identity header.
        try:
            root = self.storage_root.resolve()
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise GameFailure("storage_error") from None
        database = root / (namespace + ".sqlite")
        request = {"name": name, "arguments": arguments, "namespace": namespace, "database": str(database)}
        runner = Path(__file__).with_name("runner.mjs")
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH"}}
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.node_executable), "--disable-warning=ExperimentalWarning", str(runner),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=environment, cwd=str(runner.parent),
            )
        except OSError:
            raise GameFailure("local_runtime_failed") from None
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            # A terminated write has an unknown outcome. Never retry it here.
            if isinstance(exc, asyncio.TimeoutError):
                raise GameFailure("write_outcome_unknown") from None
            raise
        if process.returncode != 0:
            raise GameFailure("local_runtime_failed")
        try:
            envelope = json.loads(stdout)
            if not isinstance(envelope, dict) or envelope.get("ok") is not True:
                code = envelope.get("error") if isinstance(envelope, dict) else None
                raise GameFailure(code if code in {"conflict", "invalid_action", "confirmation_required",
                                                  "continuation_unavailable", "storage_error", "invalid_request"}
                                  else "local_runtime_failed")
            state = envelope["state"]
            if not isinstance(state, dict):
                raise ValueError()
        except (ValueError, KeyError):
            raise GameFailure("local_runtime_failed") from None
        # Recheck before delivery: revoked/disabled access cannot receive a
        # cached successful result. An already committed action is not replayed.
        after = self._admit(auth_context)
        if after != principal:
            raise GameDenied()
        return {"content": [{"type": "text", "text": json.dumps(state, ensure_ascii=False)}],
                "structuredContent": state, "isError": False}
