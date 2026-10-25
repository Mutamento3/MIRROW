"""send_voice 工具 — AI 发送语音消息（非阻塞异步版）

调用流程:
  AI/Flash/Pre-tool 决定发语音 → send_voice 立即返回（voice_pending=true）
  → 后台 TTS+RVC 异步生成 → 完成后找到原消息 toolCall 注入 voice_path
  → 推 chat_reload → 前端刷新 → 语音气泡出现
  → Pro 文字回复不被阻塞

防重复三层:
  1. single_use = True — 同轮只执行一次（调度器拦截）
  2. task_instruction 告知 Pro 不要写 [voice:...] 标记
  3. _clean_frontend_content 清洗漏网标记
"""
import asyncio
import os
import base64
import hashlib
import logging
import json
import copy
import re
from datetime import datetime
from .base_tool import BaseTool, ToolResult, ToolStatus

logger = logging.getLogger(__name__)

_VOICE_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "voice_manager", "output"
)


class SendVoiceTool(BaseTool):
    name = "send_voice"
    ui_only_result = True
    single_use = True  # 每轮对话只发一条语音，防止 text 参数变化绕过签名去重
    description = (
        "以语音形式发送消息给用户。当你想要用声音表达、安慰用户、"
        "或者说一些用文字表达不够温柔的话时使用。生成一段自然的中文语音。"
    )
    flash_description = "发语音——用声音跟人类伙伴说话"
    pro_guide = ("想用声音表达或人类伙伴要求语音时，调用并附自然中文口语，系统异步生成语音消息。"
                 "语音通过真实工具附件呈现，自然回复不输出 [voice:...] 等伪协议标记。")

    parameters_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要说的话。保持自然口语化，15-50字为宜。"
            },
        },
        "required": ["text"]
    }

    def __init__(self):
        super().__init__()
        self._push_reload = None  # 回调: async () → 广播 chat_reload 到所有 WS
        self._background_tasks = set()
        self._tasks_by_voice_id = {}
        self._cancelled_voice_ids = set()

    def cancel_voices(self, voice_ids: list[str]) -> None:
        """Cancel queued voice attachments after their chat source is deleted."""
        for voice_id in voice_ids:
            if not re.fullmatch(r"[0-9a-f]{12}", str(voice_id or "")):
                continue
            self._cancelled_voice_ids.add(voice_id)
            task = self._tasks_by_voice_id.get(voice_id)
            if task and not task.done():
                task.cancel()

    def set_push_reload(self, callback):
        """注入 reload 推送回调（由 main.py 注入）。"""
        self._push_reload = callback

    def get_user_facing_description(self, **kwargs) -> str:
        text = kwargs.get("text", "")[:30]
        return f"正在生成语音消息..."

    async def execute(self, text: str, **kwargs) -> ToolResult:
        try:
            voice_id = hashlib.md5(
                (text + datetime.now().isoformat()).encode()
            ).hexdigest()[:12]

            # 捕获当前 session_id（入队时就确定，不依赖后续全局变量）
            session_id = kwargs.get("_session_id", "")
            if not session_id:
                try:
                    from shared_state import _latest_session_id
                    session_id = _latest_session_id or ""
                except Exception:
                    pass

            logger.info(f"语音请求入队: voice_id={voice_id} session={session_id[:12]} text={text[:50]}...")

            # 启动后台任务：TTS + RVC → 保存 → 注入 toolCall → 推送 reload
            from .attachment_delivery import after_delivery
            task = asyncio.create_task(after_delivery(kwargs.get("_delivery_gate"),
                lambda: self._generate_voice_async(voice_id, text, session_id)))
            self._background_tasks.add(task)
            self._tasks_by_voice_id[voice_id] = task
            def _finished(done_task):
                self._background_tasks.discard(done_task)
                self._tasks_by_voice_id.pop(voice_id, None)
            task.add_done_callback(_finished)

            # 立即返回，不阻塞聊天
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content=(
                    f"语音正在后台生成中（{text[:30]}...）。"
                    f"请立即用文字自然接续对话，不要提及语音状态——语音完成后自动出现。"
                ),
                error=None,
                extra_data={
                    "voice_pending": True,
                    "attachment_kind": "voice",
                    "attachment_status": "pending",
                    "voice_id": voice_id,
                    "voice_text": text,
                }
            )

        except ImportError as e:
            logger.warning(f"语音模块不可用: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="语音模块未安装。", error=str(e))
        except Exception as e:
            logger.error(f"语音请求失败: {e}")
            return ToolResult(status=ToolStatus.ERROR, content=f"语音生成失败: {e}", error=str(e))

    # ═══════════════════════════════════════════════
    # 后台生成 + 自注入（不依赖 main.py 回调）
    # ═══════════════════════════════════════════════

    async def _generate_voice_async(self, voice_id: str, text: str, session_id: str = ""):
        """后台任务：TTS+RVC → 保存 → 找原消息 toolCall 注入 voice_path → 推 reload。"""
        try:
            from voice_manager.tts_pipeline import text_to_voice

            logger.info(f"[voice:{voice_id}] 后台生成开始: session={session_id[:12]} text={text[:50]}...")

            audio_bytes, _ext = await text_to_voice(text)  # RVC 成功→wav，回退→mp3

            if voice_id in self._cancelled_voice_ids:
                return

            os.makedirs(_VOICE_OUTPUT_DIR, exist_ok=True)
            filename = f"{voice_id}.{_ext}"
            filepath = os.path.join(_VOICE_OUTPUT_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(audio_bytes)

            if voice_id in self._cancelled_voice_ids:
                return

            voice_path = f"/voice/{filename}"
            audio_b64 = base64.b64encode(audio_bytes).decode()
            logger.info(f"[voice:{voice_id}] 生成完成: {voice_path} ({len(audio_bytes)} bytes)")

            # ── 注入：找到原消息的 send_voice toolCall，挂上 voice_path ──
            await self._inject_voice_to_message(voice_id, voice_path, audio_b64, session_id)

        except Exception as e:
            logger.error(f"[voice:{voice_id}] 后台生成失败: {e}")
            await self._mark_voice_failed(voice_id, session_id)
        finally:
            if voice_id in self._cancelled_voice_ids:
                for ext in ("wav", "mp3", "m4a"):
                    try:
                        os.remove(os.path.join(_VOICE_OUTPUT_DIR, f"{voice_id}.{ext}"))
                    except FileNotFoundError:
                        pass
                    except OSError:
                        logger.warning("cancelled voice cleanup pending: %s", voice_id)

    async def _mark_voice_failed(self, voice_id: str, session_id: str):
        """A failed async attachment must replace the visible pending state."""
        if not session_id:
            return
        from session_manager import get_global_session_manager
        from event_chronicle import get_global_chronicle
        from tool_call_utils import strip_voice_base64
        for _ in range(40):
            if voice_id in self._cancelled_voice_ids:
                return
            try:
                smgr = get_global_session_manager(); session = await smgr.get_session(session_id) if smgr else None
                for m in reversed(list(getattr(session, 'messages', []) or [])):
                    raw_tcs = m.get("toolCalls") if isinstance(m, dict) else getattr(m, "toolCalls", None)
                    serial = [copy.deepcopy(x) if isinstance(x, dict) else x.model_dump() for x in raw_tcs or []]
                    for tc in serial:
                        ed = tc.get("extra_data")
                        if isinstance(ed, dict) and ed.get("voice_id") == voice_id:
                            ed.pop("voice_pending", None); ed["attachment_status"] = "failed"; ed["attachment_error"] = "语音生成未完成，请稍后再试。"
                            tc["extra_data"] = ed
                            mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", "")
                            if not get_global_chronicle().update_attachment_if_pending(
                                session_id, mid, "send_voice", "voice_id", voice_id,
                                json.dumps(strip_voice_base64(serial), ensure_ascii=False),
                            ):
                                break
                            await smgr.update_attachment_tool_call(
                                session_id, mid, "send_voice", "voice_id", voice_id, tc,
                            )
                            if self._push_reload: await self._push_reload()
                            return
            except Exception as exc:
                logger.warning("[voice:%s] failed-state update retry: %s", voice_id, type(exc).__name__)
            await asyncio.sleep(.5)
        logger.warning("[voice:%s] failed attachment was not found after retries", voice_id)

    async def _inject_voice_to_message(self, voice_id: str, voice_path: str, audio_b64: str, session_id: str):
        """在会话中找到匹配的 toolCall（voice_pending + voice_id），注入 voice_path，同步 SQLite，推 reload。

        2026-08-23 修复断点 A/B：
        - 匹配兼容 Pydantic ToolCallRecord（session.messages 是 Pydantic 对象，非 dict）——
          旧 `tc.get("extra_data") or {} if isinstance(tc, dict) else {}` 三元优先级让 ed 恒为 {}，永不匹配。
        - 不再整表 update_session(merge=False)（会覆盖 TTS 生成期间并发新增消息）——
          改为 session JSON 锁内定点更新 + SQLite tool_calls 列同步（剥 voice_audio_base64）。
        """
        if not session_id:
            logger.warning(f"[voice:{voice_id}] 无 session_id，无法注入")
            return
        try:
            from session_manager import get_global_session_manager
            smgr = get_global_session_manager()
            if not smgr:
                return
            found = False
            matched_message_id = ""
            matched_tool_calls = []  # 注入后的 dict 版 toolCalls 列表（整条消息）
            matched_call = None
            for _ in range(40):
                if voice_id in self._cancelled_voice_ids:
                    return
                session = await smgr.get_session(session_id)
                msgs = list(getattr(session, 'messages', []) or []) if session else []
                for m in reversed(msgs):
                    raw_tcs = m.get("toolCalls") if isinstance(m, dict) else getattr(m, 'toolCalls', None)
                    tcs = [copy.deepcopy(x) if isinstance(x, dict) else x.model_dump() for x in raw_tcs or []]
                    if not tcs:
                        continue
                    mid = m.get("id") if isinstance(m, dict) else getattr(m, 'id', "")
                    for tc in tcs:
                        ed = tc.get("extra_data")
                        ed = dict(ed) if ed else {}
                        if ed.get("voice_pending") and ed.get("voice_id") == voice_id:
                            ed["voice_path"] = voice_path
                            ed["voice_audio_base64"] = audio_b64
                            ed["attachment_kind"] = "voice"
                            ed["attachment_status"] = "ready"
                            ed.pop("voice_pending", None)
                            tc["extra_data"] = ed
                            matched_message_id = mid
                            matched_tool_calls = tcs
                            matched_call = tc
                            found = True
                            break
                    if found: break
                if found: break
                await asyncio.sleep(.5)

            if not found:
                logger.warning(f"[voice:{voice_id}] 未找到匹配 toolCall (pending voice_id={voice_id})")
                return

            # SQL CAS is the commit fence.  A revoked/replaced attachment may
            # finish provider work, but cannot restore its deleted chat row.
            from event_chronicle import get_global_chronicle
            from tool_call_utils import strip_voice_base64
            chronicle = get_global_chronicle()
            cleaned = strip_voice_base64(matched_tool_calls) or []
            if not chronicle.update_attachment_if_pending(
                session_id, matched_message_id, "send_voice", "voice_id", voice_id,
                json.dumps(cleaned, ensure_ascii=False),
            ):
                return

            # 1) session JSON 锁内定点更新（不整表替换，防覆盖并发新增消息）
            try:
                _ok = await smgr.update_attachment_tool_call(
                    session_id, matched_message_id, "send_voice", "voice_id", voice_id, matched_call,
                )
                if _ok:
                    logger.info(f"[voice:{voice_id}] session toolCalls 已注入 voice_path: {voice_path}")
                else:
                    logger.warning(f"[voice:{voice_id}] session 定点更新未命中 message_id={matched_message_id}")
            except Exception as e:
                logger.error(f"[voice:{voice_id}] session 定点更新失败: {e}")

            # 2) 推送 reload
            if self._push_reload:
                try:
                    await self._push_reload()
                except Exception as e:
                    logger.error(f"[voice:{voice_id}] reload 推送失败: {e}")

        except Exception as e:
            logger.error(f"[voice:{voice_id}] 注入失败: {e}")
