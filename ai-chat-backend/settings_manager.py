"""
后端设置管理器：持久化存储 AI 人设等全局设置
"""
import os
import json
import sqlite3
import threading
import logging
from typing import Optional, Dict, Any

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")
SETTINGS_DB_FILE = os.path.join(_BASE_DIR, "events", "settings.db")
logger = logging.getLogger(__name__)
_lock = threading.Lock()
_defaults = {
    "longTextCardFullContext": False,
    "aiPersona": None,
    "userAvatar": None,
    # 环境监听：关闭 | 智能管理（默认）| 打开。隐私/工作时段始终优先。
    "ambient_listening_mode": "smart",
    "ambient_pc_microphone": "",  # 留空时仍由手机采集；选定电脑输入设备后启用自动路由
    "ambient_listening_privacy_windows": [],
    "ambient_listening_thresholds": {
        "idle_minutes": 10,
        "immediate_chars": 500,
        "timed_min_chars": 80,
        "timed_seconds": 120,
        "cooldown_seconds": 180,
    },
    "phone_browse_interval": 5,  # 刷手机模式截图间隔（分钟），默认5，范围1-10
    "dnd_enabled": False,  # 首次使用的明确缺省；坏数据仍保持未知
    "phone_browse_mode": False,  # 刷手机模式开关状态（持久化，重启恢复）
    # ── 音乐耳蜗 ──
    "music_cochlea_enabled": True,        # 音乐耳蜗总开关
    "music_mode_state": False,            # 音乐模式开关状态（持久化，重启恢复）
    "music_source_priority": "mobile",    # 采集源优先级: "mobile" | "pc"
    "music_idle_warning_minutes": 30,     # 无音乐提醒阈值（分钟）
    "music_push_no_reply_minutes": 10,    # 推送后无回复阈值（分钟）
    # ── 漫想听歌 ──
    "listen_music_playback": False,       # LISTEN_MUSIC 漫想事件是否实际播放音频（仪式感）
    "listen_music_playback_device": "computer",  # computer | mobile；只在真实外放开启时生效
    # ── 截断参数（由 truncation_config.py 管理元数据）──
    "topic_memory_cap": 0,       # 话题记忆整合（0=无限制）
    "diary_msg_cap": 0,          # 日记单条消息（0=无限制）
    "diary_total_cap": 0,        # 日记总长度（0=无限制）
    "yesterday_cap": 0,          # 昨日对话注入（0=无限制）
    "memory_inject_cap": 0,      # 记忆碎片注入（0=无限制）
    "ob_merge_cap": 0,           # 记忆合并更新（0=无限制）
    "brain_doc_cap": 0,          # 大脑文档生成（0=无限制）
    "conv_msg_cap": 300,         # 历史对话回顾
    "flash_user_cap": 300,       # 工具触发·用户消息
    "flash_ai_cap": 400,         # 工具触发·AI回复
    "flash_reasoning_cap": 200,  # 工具触发·思考过程
    "flash_tools_cap": 0,        # 工具触发·工具描述（0=无限制，靠 flash_description 精简控制体积）
    "phone_browse_tokens": 200,  # 陪刷回复长度
    "sentinel_tokens": 150,      # 哨兵告警长度
    "bookmark_cap": 500,         # 收藏夹内容
    "ledger_ctx_cap": 3000,      # 记账本上下文
    "music_analyzer_cap": 8000,  # 音乐分析数据
    "persona_anchor_cap": 200,   # 人设锚点
    "persona_simplified_cap": 200,  # 人设简化
    "query_enhance_cap": 200,    # 搜索查询增强
    "wander_query_cap": 200,     # 漫想搜索查询
    # ── 时光回溯 Scene ──
    "scene_gap_minutes": 15,      # 用户消息断档阈值（分钟）
    "scene_long_message_threshold": 50,
    "active_anchor_limit": 50,    # 日常语义检索参与的活跃锚点池容量
    # ── 对话约定：约定词为空时禁用，时间支持跨午夜 ──
    "conversation_covenant": {
        "word": "",
        "start_time": "00:00",
        "end_time": "23:59",
        "duration_minutes": 30,
    },
}


class SettingsReadError(RuntimeError):
    """设置源不可读；调用方不能据此回写默认值。"""


def _load_json() -> Dict[str, Any]:
    """仅真实缺文件允许首次缺省；损坏与读取失败保留原文件。"""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        raise SettingsReadError("设置文件读取失败，原文件已保留。") from None
    if not isinstance(data, dict):
        raise SettingsReadError("设置文件读取失败，原文件已保留。")
    return data


def _decode_settings_rows(rows) -> Dict[str, Any]:
    data = dict(_defaults)
    for key, value_json in rows:
        try:
            data[key] = json.loads(value_json)
        except (TypeError, ValueError, UnicodeError):
            raise SettingsReadError("设置数据库读取失败，原数据库已保留。") from None
    return data


def _open_db() -> sqlite3.Connection:
    """兼容首次迁移；初始化与导入同事务，已初始化空表不再导入。"""
    connection = None
    try:
        os.makedirs(os.path.dirname(SETTINGS_DB_FILE), exist_ok=True)
        connection = sqlite3.connect(SETTINGS_DB_FILE, timeout=10)
        metadata_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings_metadata'"
        ).fetchone()
        if metadata_exists:
            marker = connection.execute(
                "SELECT value FROM settings_metadata WHERE key='initialized'"
            ).fetchone()
            if marker:
                if marker[0] != "1":
                    raise SettingsReadError("设置数据库初始化状态异常，原数据库已保留。")
                return connection
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS settings_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        # Recheck under the initialization transaction before consulting legacy.
        marker = connection.execute("SELECT value FROM settings_metadata WHERE key='initialized'").fetchone()
        if marker and marker[0] != "1":
            raise SettingsReadError("设置数据库初始化状态异常，原数据库已保留。")
        if not marker:
            rows = connection.execute("SELECT key, value_json FROM settings").fetchall()
            _decode_settings_rows(rows)
            legacy = _load_json() if not rows else {}
            if legacy:
                connection.executemany(
                    "INSERT INTO settings(key, value_json) VALUES (?, ?)",
                    [(key, json.dumps(value, ensure_ascii=False)) for key, value in legacy.items()],
                )
            connection.execute("INSERT INTO settings_metadata(key, value) VALUES ('initialized', '1')")
        connection.commit()
        return connection
    except (sqlite3.Error, OSError):
        if connection is not None:
            connection.close()
        raise SettingsReadError("设置数据库读取失败，原数据库已保留。") from None
    except BaseException:
        if connection is not None:
            connection.close()
        raise


def _load() -> Dict[str, Any]:
    """SQLite 是设置权威；坏行不冒充默认值，读取失败不允许回写。"""
    connection = _open_db()
    try:
        rows = connection.execute("SELECT key, value_json FROM settings").fetchall()
    except sqlite3.Error:
        raise SettingsReadError("设置数据库读取失败，原数据库已保留。") from None
    finally:
        connection.close()
    return _decode_settings_rows(rows)


def _save(data: Dict[str, Any]):
    """原子保存设置到文件（写临时文件→rename，防止写入中途崩溃损坏文件）"""
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)  # atomic on POSIX/Windows


def get_settings() -> Dict[str, Any]:
    """获取所有设置"""
    with _lock:
        return _load()


def get_setting(key: str):
    """获取单个设置"""
    with _lock:
        return _load().get(key)


def set_setting(key: str, value) -> None:
    """权威提交确认成功；兼容 JSON 投影失败单独记录，不误报未保存。"""
    with _lock:
        data = _load()
        data[key] = value
        value_json = json.dumps(value, ensure_ascii=False)
        connection = _open_db()
        try:
            connection.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP",
                (key, value_json),
            )
            connection.commit()
        except sqlite3.Error:
            raise RuntimeError("设置数据库保存未获确认。") from None
        finally:
            connection.close()
        try:
            _save(data)
        except OSError as exc:
            category = type(exc).__name__
            logger.warning("settings_json_projection_failed error_type=%s", category if category in {"PermissionError", "FileNotFoundError", "OSError"} else "OSError")


def get_ai_persona_simplified() -> str:
    """获取简化版 AI 人设，用于日记 prompt 注入"""
    persona = get_setting("aiPersona")
    if not persona:
        return ""
    if isinstance(persona, dict):
        name = persona.get("name", "")
        traits = persona.get("traits", "")
        style = persona.get("style", "")
        parts = [p for p in [name, traits, style] if p]
        return "、".join(parts)
    if isinstance(persona, str):
        from truncation_config import get_truncation_limit
        _psc = get_truncation_limit("persona_simplified_cap")
        if _psc > 0 and len(persona) > _psc:
            return persona[:_psc]
        return persona
    return ""
