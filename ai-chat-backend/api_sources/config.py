"""One .env authority; keys are write-only and never appear in public state.

MIRROW_API_ROUTING is non-secret JSON. Credentials live in the referenced env
variables. No separate frontend/localStorage copy competes with backend state.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import GROUPS, GROUP_IDS, NODE_LABELS, PROFILES, group_for, legacy_group_for, public_groups
from .policies import CategoryPolicy, select_ids, supported_roles, route_role, validate_policies, public_categories

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
CONFIG_ENV = "MIRROW_API_ROUTING"


class ModelSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    label: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    profile: str = "generic"
    json_mode: str = "prompt"
    thinking: str = "default"
    system_role: str = "system"
    sampling: bool = True
    enabled: bool = True
    roles: list[str] = Field(default_factory=list, max_length=3)
    allowed_nodes: list[str] = Field(default_factory=list, max_length=80)

    @field_validator('allowed_nodes')
    @classmethod
    def valid_nodes(cls, values):
        if len(set(values)) != len(values) or not set(values) <= set(NODE_LABELS):
            raise ValueError('无效适用节点')
        return values

    @field_validator("roles")
    @classmethod
    def valid_roles(cls, values):
        if not set(values) <= {"main", "expression", "internal"} or len(set(values)) != len(values):
            raise ValueError("无效模型职责")
        return values

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value):
        if value not in PROFILES:
            raise ValueError("未知模型适配类型")
        return value

    @field_validator("json_mode", "thinking", "system_role")
    @classmethod
    def valid_option(cls, value, info):
        options = {"json_mode": {"prompt", "json_object"}, "thinking": {"default", "off", "on"},
                   "system_role": {"system", "developer", "user"}}
        if value not in options[info.field_name]:
            raise ValueError("未知适配选项")
        return value


class Station(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    protocol: str = "openai-chat"
    key_env: str = Field(pattern=r"^(?:[A-Z][A-Z0-9_]{1,70}_API_KEY|MIRROW_RELAY_KEY_[A-Z0-9_]{1,60})$")
    api_key: str | None = Field(default=None, max_length=1024, exclude=True, repr=False)
    enabled: bool = True
    models: list[ModelSource] = Field(default_factory=list, max_length=100)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("接口地址需为不含凭据和查询参数的 HTTP(S) 地址")
        return value.strip().rstrip("/")

    @field_validator("protocol")
    @classmethod
    def valid_protocol(cls, value):
        if value not in {"openai-chat", "openai-responses", "gemini-native"}:
            raise ValueError("未知接口协议")
        return value

    @model_validator(mode="after")
    def normalize_endpoint(self):
        # Legacy env values and saved settings may contain a complete endpoint.
        # Each adapter appends its own operation; keep the provider root intact.
        suffixes = {
            "openai-chat": ("/chat/completions", "/models"),
            "openai-responses": ("/responses", "/models"),
        }.get(self.protocol, ())
        for suffix in suffixes:
            if self.base_url.endswith(suffix):
                self.base_url = self.base_url[:-len(suffix)]
                break
        return self


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stations: list[Station] = Field(default_factory=list, max_length=100)
    chains: dict[str, list[str]] = Field(default_factory=dict)
    overrides: dict[str, list[str]] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=90, ge=5, le=600)
    policies: dict[str, CategoryPolicy] = Field(default_factory=dict)
    assignments: dict[str, str] = Field(default_factory=dict)


class ConfigError(ValueError):
    pass


class ConfigConflict(ConfigError):
    pass


def _environment(path: Path = ENV_PATH) -> dict[str, str]:
    # Disk wins so manual edits are observed by the next task without restarting.
    return {**os.environ, **{k: v for k, v in dotenv_values(path).items() if v is not None}}


def _defaults(env: dict) -> dict:
    qwen = env.get("SILICONFLOW_STRUCTURED_MODEL_QWEN3_6") or "Qwen/Qwen3.6-35B-A3B"
    flash = env.get("SILICONFLOW_STRUCTURED_MODEL_V4_FLASH") or "deepseek-ai/DeepSeek-V4-Flash"
    selected = env.get("SILICONFLOW_STRUCTURED_MODEL_PROFILE", "").lower()
    first = "sf_flash" if selected in {"v4", "v4_flash", "deepseek_v4_flash"} else "sf_qwen"
    models = [dict(id="sf_qwen", label="Qwen", model=qwen, profile="siliconflow-qwen", json_mode="json_object", thinking="off"),
              dict(id="sf_flash", label="DS Flash", model=flash, profile="siliconflow-deepseek", json_mode="json_object", thinking="off")]
    legacy = env.get("SILICONFLOW_STRUCTURED_MODEL")
    if selected in {"v3.2", "v3_2"}:
        legacy = env.get("SILICONFLOW_STRUCTURED_MODEL_V3_2") or "deepseek-ai/DeepSeek-V3.2"
    if legacy and legacy not in {qwen, flash} and selected not in {"qwen3.6", "qwen3_6", "qwen3.6_35b_a3b", "qwen3_6_35b_a3b", "v4", "v4_flash", "deepseek_v4_flash"}:
        models.append(dict(id="sf_legacy", label="现有结构化模型", model=legacy, profile="siliconflow-deepseek", json_mode="json_object", thinking="off"))
        first = "sf_legacy"
    key_name = "DEEPSEEK_FLASH_API_KEY" if env.get("DEEPSEEK_FLASH_API_KEY") else "DEEPSEEK_API_KEY"
    chain = [first, *[key for key in ("sf_qwen", "sf_flash") if key != first], "direct_flash"]
    if not env.get("SILICONFLOW_STRUCTURED_API_KEY"):
        chain = ["direct_flash"]
    return {
        "stations": [
            dict(id="siliconflow", name="硅基流动", base_url=env.get("SILICONFLOW_STRUCTURED_API_URL") or "https://api.siliconflow.cn/v1", protocol="openai-chat", key_env="SILICONFLOW_STRUCTURED_API_KEY", models=models),
            dict(id="deepseek_direct", name="DeepSeek 官方", base_url=env.get("DEEPSEEK_FLASH_API_URL") or env.get("DEEPSEEK_API_URL") or "https://api.deepseek.com/v1", protocol="openai-chat", key_env=key_name,
                 models=[dict(id="direct_flash", label="现有 Flash", model=env.get("DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash-vision-exp", profile="deepseek", thinking="off", json_mode="json_object")]),
        ], "chains": {"structured": chain, "cognition": chain}, "overrides": {}, "timeout_seconds": 90,
    }


def validate_config(config: RoutingConfig) -> None:
    ids = [site.id for site in config.stations]
    models = [model.id for site in config.stations for model in site.models]
    if len(set(ids)) != len(ids) or len(set(models)) != len(models):
        raise ConfigError("站点和模型 ID 必须各自唯一")
    if set(config.chains) != GROUP_IDS:
        raise ConfigError("任务分组已升级，请重新载入配置后再保存全部分组顺位")
    known = set(models)
    for chain in [*config.chains.values(), *config.overrides.values()]:
        if not chain or len(chain) > 32 or len(set(chain)) != len(chain) or not set(chain) <= known:
            raise ConfigError("每份顺位应包含 1–32 个不同且存在的来源")
    for node in config.overrides:
        if node not in NODE_LABELS:
            raise ConfigError("无效节点 ID")
        from .catalog import SINGLE_SOURCE_NODES
        if node in SINGLE_SOURCE_NODES and len(config.overrides[node]) != 1:
            raise ConfigError("独立复审节点必须指定一个来源")
        if node in SINGLE_SOURCE_NODES:
            chosen = next(m for s in config.stations for m in s.models if m.id == config.overrides[node][0])
            if ('internal' not in supported_roles(chosen.model_dump())
                    or (chosen.allowed_nodes and node not in chosen.allowed_nodes)):
                raise ConfigError('所选来源不适用于此内部复审节点')
    try:
        validate_policies(config, known)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None


def normalize_legacy_groups(config: RoutingConfig) -> RoutingConfig:
    """Read old two-group files without writing or changing any effective chain."""
    if set(config.chains) != {"structured", "cognition"}:
        return config
    updated = config.model_copy(deep=True)
    updated.chains = {}
    for ident, _, _, _, nodes, _ in GROUPS:
        inherited = config.chains[legacy_group_for(nodes[0])]
        updated.chains[ident] = list(inherited)
        for node in nodes:
            original = config.chains[legacy_group_for(node)]
            if original != inherited and node not in updated.overrides:
                updated.overrides[node] = list(original)
    return updated


def add_builtin_sources(config: RoutingConfig, env: dict) -> RoutingConfig:
    """Expose existing official brains without changing any saved chain."""
    updated = config.model_copy(deep=True)
    known = {m.id for s in updated.stations for m in s.models}
    station_ids = {s.id for s in updated.stations}
    if ("direct_pro" not in known and "deepseek_main" in station_ids) or ("direct_flash" not in known and "deepseek_expression" in station_ids):
        raise ConfigError("官方预设站点 ID 与现有来源冲突，请更换自定义站点 ID")
    if "direct_pro" not in known:
        updated.stations.append(Station(id="deepseek_main", name="DS 官方 · Pro", base_url=env.get("DEEPSEEK_API_URL") or "https://api.deepseek.com/v1",
            key_env="DEEPSEEK_API_KEY", models=[ModelSource(id="direct_pro", label="DS Pro", model=env.get("DEEPSEEK_MODEL") or "deepseek-v4-pro", profile="deepseek", thinking="default", roles=["main"])]))
    if "direct_flash" not in known:
        updated.stations.append(Station(id="deepseek_expression", name="DS 官方 · Flash", base_url=env.get("DEEPSEEK_FLASH_API_URL") or env.get("DEEPSEEK_API_URL") or "https://api.deepseek.com/v1",
            key_env="DEEPSEEK_FLASH_API_KEY" if env.get("DEEPSEEK_FLASH_API_KEY") else "DEEPSEEK_API_KEY", models=[ModelSource(id="direct_flash", label="DS Flash", model=env.get("DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash-vision-exp", profile="deepseek", thinking="default", roles=["internal", "expression"])]))
    for ident in GROUP_IDS - set(updated.chains):
        updated.chains[ident] = ["direct_pro" if ident == "main_chat" else "direct_flash"]
    return updated


def load_config(path: Path = ENV_PATH) -> tuple[RoutingConfig, dict[str, str], str]:
    env = _environment(path)
    raw = env.get(CONFIG_ENV)
    try:
        data = json.loads(raw) if raw else _defaults(env)
        config = add_builtin_sources(normalize_legacy_groups(RoutingConfig.model_validate(data)), env)
        validate_config(config)
    except Exception as exc:
        # Never echo validation input: it may have been manually supplied with keys.
        raise ConfigError("API 配置无效，请检查后端 MIRROW_API_ROUTING") from exc
    revision_material = config.model_dump()
    revision_material["credentials"] = {site.key_env: hashlib.sha256(env.get(site.key_env, "").encode()).hexdigest() for site in config.stations}
    revision = hashlib.sha256(json.dumps(revision_material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return config, env, revision


def public_config(path: Path = ENV_PATH) -> dict:
    config, env, revision = load_config(path)
    result = config.model_dump()
    for station in result["stations"]:
        station["key_configured"] = bool(env.get(station["key_env"], "").strip())
    return {**result, "revision": revision, "groups": public_groups(), "categories": public_categories(config), "profiles": PROFILES,
            "stable_sources": {"main": "direct_pro", "expression": "direct_flash", "internal": "direct_flash"}}


def save_config(config: RoutingConfig, revision: str, path: Path = ENV_PATH) -> dict:
    # Share the existing main-model writer lock; either settings surface preserves
    # the other's env fields. Revision checks protect two in-flight settings saves.
    from main_model_profiles import _ENV_LOCK
    validate_config(config)
    with _ENV_LOCK:
        _, env, current_revision = load_config(path)
        if revision != current_revision:
            raise ConfigConflict("配置已变化，请重新载入后再保存")
        updates = {CONFIG_ENV: json.dumps(config.model_dump(), ensure_ascii=False, separators=(",", ":"))}
        for station in config.stations:
            if station.api_key is not None and station.api_key.strip():
                if "\n" in station.api_key or "\r" in station.api_key:
                    raise ConfigError("密钥不能包含换行")
                updates[station.key_env] = station.api_key.strip()
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        rendered = []
        remaining = dict(updates)
        for line in original.splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
            if match and match.group(1) in updates:
                key = match.group(1)
                if key in remaining:
                    rendered.append(f"{key}=" + "'" + remaining.pop(key).replace("\\", "\\\\").replace("'", "\\'") + "'")
            else:
                rendered.append(line)
        for key, value in remaining.items():
            rendered.append(f"{key}=" + "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".api-routing-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write("\n".join(rendered) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        # Existing imported consumers keep their credential values up to date.
        os.environ.update(updates)
    return public_config(path)


def resolve_chain(route: str, path: Path = ENV_PATH, *, now=None) -> tuple[list[dict], int]:
    from .catalog import SINGLE_SOURCE_NODES
    if route in SINGLE_SOURCE_NODES:
        raise ConfigError('独立复审节点须使用单来源调用，不参与顺位回退')
    from .scope import current_snapshot, selection_time
    snapshot = current_snapshot() if path == ENV_PATH else None
    config, env, revision = snapshot or load_config(path)
    chain, selection = select_ids(config, route, now or (selection_time() if snapshot else None))
    managed = selection["inherited"] or (selection["period"] == "override" and
        (selection["category"] in config.policies or route_role(route) in {"main", "expression"}))
    selection["managed"] = managed
    if managed:
        stable = "direct_pro" if route_role(route) == "main" else "direct_flash"
        chain = [key for key in chain if key != stable] + [stable]
    indexed = {model.id: (station, model) for station in config.stations for model in station.models}
    result = []
    for ordinal, source_id in enumerate(chain, 1):
        station, model = indexed[source_id]
        if not station.enabled or not model.enabled:
            continue
        if model.allowed_nodes and route not in model.allowed_nodes:
            continue
        if managed and route_role(route) not in supported_roles(model.model_dump()):
            continue
        result.append({"station": copy.deepcopy(station.model_dump()), "model": copy.deepcopy(model.model_dump()),
                       "api_key": env.get(station.key_env, ""), "ordinal": ordinal,
                       "selection": {**selection, "revision": revision},
                       "availability_managed": True,
                       "stable": source_id == ("direct_pro" if route_role(route) == "main" else "direct_flash")})
    if managed:
        for ordinal, source in enumerate(result, 1):
            source["ordinal"] = ordinal
    return result, config.timeout_seconds
