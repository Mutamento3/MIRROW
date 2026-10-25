"""Windows 开机自启管理 — 通过注册表 Run 键实现"""
import winreg
import logging

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

MIRROW_ENTRY = "MIRROW"

MIRROW_COMMAND = r'powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File ./MIRROW\start-all.ps1'

ENTRIES = {
    "mirrow": {"name": MIRROW_ENTRY, "command": MIRROW_COMMAND},
}


def get_status(name: str) -> dict:
    """查询注册表中指定名称的自启状态"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return {"enabled": True, "command": value}
    except FileNotFoundError:
        return {"enabled": False, "command": None}
    except Exception as e:
        logger.warning(f"查询自启状态失败 [{name}]: {e}")
        return {"enabled": False, "command": None}


def enable(name: str, command: str) -> dict:
    """注册开机自启项。返回 {ok, error?}"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)

        # 回读验证
        status = get_status(name)
        if not status["enabled"]:
            return {"ok": False, "error": "注册表写入成功但回读失败——请检查权限"}
        logger.info(f"自启已注册: {name} → {command}")
        return {"ok": True}
    except PermissionError:
        return {"ok": False, "error": "权限不足——请以管理员身份运行"}
    except Exception as e:
        logger.error(f"注册自启失败 [{name}]: {e}")
        return {"ok": False, "error": str(e)}


def disable(name: str) -> dict:
    """删除开机自启项。返回 {ok, error?}"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)

        # 回读验证
        status = get_status(name)
        if status["enabled"]:
            return {"ok": False, "error": "注册表删除成功但回读仍存在——请重试"}
        logger.info(f"自启已取消: {name}")
        return {"ok": True}
    except FileNotFoundError:
        # 键本来就不存在，也算成功
        return {"ok": True}
    except PermissionError:
        return {"ok": False, "error": "权限不足——请以管理员身份运行"}
    except Exception as e:
        logger.error(f"取消自启失败 [{name}]: {e}")
        return {"ok": False, "error": str(e)}


def get_all_status() -> dict:
    """获取所有自启项的状态"""
    return {
        "mirrow": get_status(MIRROW_ENTRY),
    }


def ensure_defaults() -> dict:
    """
    确保默认开机自启已设置（首次运行自动注册，命令变更时自动更新）。
    返回 {component: "registered"|"skipped"|"updated"|"failed"}
    """
    result = {}
    for key, info in ENTRIES.items():
        name = info["name"]
        command = info["command"]
        status = get_status(name)
        if status["enabled"]:
            if status["command"] == command:
                result[key] = "skipped"        # 已存在且命令一致
            else:
                # 命令已变更，更新注册表
                logger.info(f"自启命令已变更，更新: {name}")
                r = enable(name, command)
                result[key] = "updated" if r["ok"] else f"update failed: {r.get('error','?')}"
        else:
            r = enable(name, command)
            result[key] = "registered" if r["ok"] else f"failed: {r.get('error','?')}"
    return result
