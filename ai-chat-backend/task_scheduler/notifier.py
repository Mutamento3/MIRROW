"""
定时任务管理器 - 桌面通知

支持 Windows Toast 通知（winotify），安装后备为非阻塞 ctypes MessageBox。
Bug ① 修复：增加异步包装器 + 详细错误日志。
"""
import asyncio
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_HAS_WINOTIFY = False
try:
    from winotify import Notification, audio
    _HAS_WINOTIFY = True
except ImportError:
    logger.info("winotify 未安装，Windows 桌面通知将使用 ctypes 后备方案。安装: pip install winotify")


def send_desktop_notification(title: str, message: str) -> bool:
    """
    发送 Windows 桌面通知（同步版本，保留向后兼容）

    Returns:
        是否成功发送
    """
    if sys.platform != "win32":
        logger.debug(f"非 Windows 平台，跳过桌面通知: {title}")
        return False

    if _HAS_WINOTIFY:
        try:
            toast = Notification(
                app_id="MIRROW",
                title=title,
                msg=message,
                duration="short",
            )
            toast.set_audio(audio.Default, loop=False)
            toast.show()
            logger.info(f"Windows Toast 通知已发送: {title}")
            return True
        except Exception as e:
            logger.warning(f"winotify 发送失败（将回退 ctypes）: {type(e).__name__}: {e}")

    # ctypes 后备方案（在后台线程运行，避免阻塞调度器）
    try:
        def _show_message_box():
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, message, title, 0x40 | 0x1000  # MB_ICONINFORMATION | MB_SYSTEMMODAL
            )

        t = threading.Thread(target=_show_message_box, daemon=True)
        t.start()
        logger.info(f"ctypes 桌面通知已发送（后台线程）: {title}")
        return True
    except Exception as e:
        logger.error(f"桌面通知完全失败 ({type(e).__name__}): {e}")
        return False


async def send_desktop_notification_async(title: str, message: str) -> bool:
    """
    Bug ① 修复：异步包装器，不阻塞事件循环。
    通过 asyncio.to_thread() 在线程池中执行通知。
    """
    if sys.platform != "win32":
        return False
    try:
        return await asyncio.to_thread(send_desktop_notification, title, message)
    except Exception as e:
        logger.error(f"异步桌面通知失败: {type(e).__name__}: {e}")
        return False
