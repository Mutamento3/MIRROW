"""Main-chat model profiles and safe ``.env`` selection persistence.

Provider credentials remain backend-only.  The frontend receives stable profile
ids, labels and capability/availability flags, never keys or provider URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Mapping


DEFAULT_PROFILE_ID = "deepseek-pro"
ACTIVE_PROFILE_ENV = "MAIN_CHAT_MODEL_PROFILE"
_ENV_PATH = Path(__file__).with_name(".env")
_ENV_LOCK = threading.RLock()


@dataclass(frozen=True)
class MainModelProfile:
    id: str
    label: str
    provider: str
    model: str
    api_key: str
    base_url: str
    supports_vision: bool
    missing_env: tuple[str, ...] = ()
    station_id: str = ""
    station_name: str = ""
    group_id: str = ""
    group_label: str = ""
    source_label: str = ""
    billing_label: str = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "supports_vision": self.supports_vision,
            "available": self.available,
            "missing_env": list(self.missing_env) if not self.available else [],
            "group_id": self.group_id or self.id,
            "group_label": self.group_label or self.label,
            "source_label": self.source_label,
            "billing_label": self.billing_label,
        }


def normalize_openai_base_url(url: str) -> str:
    """Convert a configured endpoint into the base URL expected by AsyncOpenAI."""
    value = str(url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _missing(env: Mapping[str, str], *names: str) -> tuple[str, ...]:
    return tuple(name for name in names if not str(env.get(name) or "").strip())


def _relay55_profile(env: Mapping[str, str], *, metered: bool = False) -> MainModelProfile:
    """Reuse the registered station's endpoint/key as one owning tuple."""
    from api_sources.config import Station

    station = None
    try:
        data = json.loads(env.get("MIRROW_API_ROUTING") or "{}")
        raw = next((item for item in data.get("stations", []) if item.get("id") == "relay55"), None)
        station = Station.model_validate(raw) if raw else None
    except (ValueError, TypeError, AttributeError):
        pass
    usable = bool(station and station.enabled and station.protocol == "openai-chat")
    key = str(env.get(station.key_env) or "").strip() if usable else ""
    missing = () if key else (station.key_env if usable else "55 站点的 OpenAI Chat 配置",)
    return MainModelProfile(
        id="relay55-gemini-3.8-flash-metered" if metered else "relay55-gemini-3.8-flash",
        label=f"55 · Gemini 3.8 Flash high · {'按量' if metered else '按次'}",
        provider="openai-compatible",
        model="逆[Ag1-量-0.2x]gemini-3.8-flash-high" if metered else "逆[Ag1-次-0.02￥]gemini-3.8-flash-high",
        api_key=key,
        base_url=normalize_openai_base_url(station.base_url) if usable else "",
        supports_vision=True,
        missing_env=missing,
        station_id="relay55",
        station_name=station.name if station else "55",
        group_id="gemini-3.8-flash",
        group_label="Gemini 3.8 Flash",
        source_label="55",
        billing_label="按量 · high" if metered else "按次 · high",
    )


def get_main_model_profiles(env: Mapping[str, str] | None = None) -> dict[str, MainModelProfile]:
    source = os.environ if env is None else env
    deepseek_url = str(source.get("DEEPSEEK_API_URL") or "https://api.deepseek.com/v1/chat/completions")
    flash_url = str(source.get("DEEPSEEK_FLASH_API_URL") or deepseek_url)
    flash_model = str(source.get("DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash-vision-exp")
    flash_key_env = "DEEPSEEK_FLASH_API_KEY" if str(source.get("DEEPSEEK_FLASH_API_KEY") or "").strip() else "DEEPSEEK_API_KEY"
    crazyrouter_url = str(source.get("CRAZYROUTER_API_URL") or "https://cn.crazyrouter.com/v1/chat/completions")
    glm_url = str(source.get("GLM_API_URL") or "https://open.bigmodel.cn/api/paas/v4/chat/completions")

    return {
        "deepseek-pro": MainModelProfile(
            id="deepseek-pro",
            label="DS Pro",
            provider="deepseek",
            model=str(source.get("DEEPSEEK_MODEL") or "deepseek-v4-pro"),
            api_key=str(source.get("DEEPSEEK_API_KEY") or ""),
            base_url=normalize_openai_base_url(deepseek_url),
            supports_vision=False,
            missing_env=_missing(source, "DEEPSEEK_API_KEY"),
            source_label="DeepSeek 配置线路",
        ),
        "deepseek-flash": MainModelProfile(
            id="deepseek-flash",
            label="DS 官方 Flash",
            provider="deepseek",
            model=flash_model,
            api_key=str(source.get(flash_key_env) or ""),
            base_url=normalize_openai_base_url(flash_url),
            supports_vision="vision" in flash_model.lower(),
            missing_env=_missing(source, flash_key_env),
            station_id="deepseek_direct",
            station_name="DeepSeek 官方",
            source_label="DeepSeek 官方",
            billing_label="按量",
        ),
        "gemini-3.8-flash": MainModelProfile(
            id="gemini-3.8-flash",
            label="Gemini 3.8 Flash",
            provider="openai-compatible",
            model=str(source.get("CRAZYROUTER_GEMINI_MODEL") or "gemini-3.8-flash"),
            api_key=str(source.get("CRAZYROUTER_API_KEY") or ""),
            base_url=normalize_openai_base_url(crazyrouter_url),
            supports_vision=True,
            missing_env=_missing(source, "CRAZYROUTER_API_KEY"),
            source_label="CrazyRouter",
            billing_label="按量",
        ),
        "glm-5.3-flash": MainModelProfile(
            id="glm-5.3-flash",
            label="GLM 5.3 Flash",
            provider="openai-compatible",
            model=str(source.get("GLM_CHAT_MODEL") or "glm-5.3-flash"),
            api_key=str(source.get("GLM_API_KEY") or ""),
            base_url=normalize_openai_base_url(glm_url),
            supports_vision=True,
            missing_env=_missing(source, "GLM_API_KEY"),
            source_label="智谱配置线路",
        ),
        "relay55-gemini-3.8-flash": _relay55_profile(source),
        "relay55-gemini-3.8-flash-metered": _relay55_profile(source, metered=True),
    }


def get_main_model_profile(profile_id: str, env: Mapping[str, str] | None = None) -> MainModelProfile:
    profiles = get_main_model_profiles(env)
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise ValueError(f"未知主聊天模型配置: {profile_id}") from exc


def get_configured_profile_id(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    requested = str(source.get(ACTIVE_PROFILE_ENV) or DEFAULT_PROFILE_ID).strip()
    return requested if requested in get_main_model_profiles(source) else DEFAULT_PROFILE_ID


def resolve_startup_profile(env: Mapping[str, str] | None = None) -> MainModelProfile:
    """Keep an explicit selection across restart, including unavailable profiles."""
    source = os.environ if env is None else env
    profiles = get_main_model_profiles(source)
    requested = get_configured_profile_id(source)
    return profiles[requested]


def persist_main_model_profile(profile_id: str, env_path: Path | str = _ENV_PATH) -> None:
    """Persist only the non-secret active profile id, preserving all other env lines."""
    get_main_model_profile(profile_id)
    target = Path(env_path)
    with _ENV_LOCK:
        original = target.read_text(encoding="utf-8") if target.exists() else ""
        newline = "\r\n" if "\r\n" in original else "\n"
        lines = original.splitlines()
        replacement = f"{ACTIVE_PROFILE_ENV}={profile_id}"
        updated: list[str] = []
        replaced = False
        for line in lines:
            if line.lstrip().startswith(f"{ACTIVE_PROFILE_ENV}="):
                if not replaced:
                    updated.append(replacement)
                    replaced = True
                continue
            updated.append(line)
        if not replaced:
            if updated and updated[-1].strip():
                updated.append("")
            updated.append(replacement)
        rendered = newline.join(updated) + newline

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        os.environ[ACTIVE_PROFILE_ENV] = profile_id


def public_main_model_state(active_profile_id: str | None = None) -> dict:
    profiles = get_main_model_profiles()
    active = active_profile_id or get_configured_profile_id()
    if active not in profiles:
        active = DEFAULT_PROFILE_ID
    return {
        "active_profile_id": active,
        "profiles": [profile.public_dict() for profile in profiles.values()],
    }


__all__ = [
    "ACTIVE_PROFILE_ENV",
    "DEFAULT_PROFILE_ID",
    "MainModelProfile",
    "get_configured_profile_id",
    "get_main_model_profile",
    "get_main_model_profiles",
    "normalize_openai_base_url",
    "persist_main_model_profile",
    "public_main_model_state",
    "resolve_startup_profile",
]
