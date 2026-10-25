"""
👁️ Eyes — AI 的视觉感官（采集与分析分层版本）

检查点前缀:
  [EYES:1] — execute() 入口/出口
  [EYES:2] — 各路采集 入口/出口/中间状态
  [EYES:3] — 原图交付统一视觉轮
"""
import asyncio
import time as _time
from typing import Dict, Any, List, Optional

from .base_tool import BaseTool, ToolResult, ToolStatus
from context_scheduler_log import write_log as _log

_SOURCE_TIMEOUT = 18
_PC_SOURCE_TIMEOUT = 15

# ─── AI 的观看意图提取（纯规则，零 LLM）─────────────────────

_INTENT_RULES = [
    # (关键词列表, 聚合指令)
    (["后面", "背后", "身后", "周围", "环境", "在哪", "哪里", "外面", "旁边"],
      "优先关注摄像头拍摄的物理环境和人物位置"),
    (["表情", "脸", "样子", "自拍", "前置", "正脸", "看镜头", "本人", "打扮", "穿搭", "穿什么"],
     "优先关注手机前置摄像头（自拍画面），关注人类伙伴本人的表情和状态"),
    (["后面有人", "跟踪", "谁在", "有没有人", "安全"],
     "优先关注摄像头中的其他人——有没有人、谁、在哪里"),
]


def _derive_intent_rule(analysis_prompt: str) -> str:
    """从 AI 的 analysis_prompt 提取观看意图，返回聚合优先级指令。纯规则，零延迟。"""
    if not analysis_prompt:
        return "均衡考虑所有可用来源，描述最重要的发现"
    prompt_lower = analysis_prompt.lower()
    for keywords, rule in _INTENT_RULES:
        for kw in keywords:
            if kw in prompt_lower:
                return rule
    return "均衡考虑所有可用来源，描述最重要的发现"


class EyesTool(BaseTool):
    name = "eyes"
    description = (
        "查看人类伙伴本人和周围物理环境。系统会尝试手机前后摄与PC摄像头，"
        "融合分析后告诉你看到了什么；不包含手机或电脑屏幕内容。参数只有分析重点。"
    )
    flash_description = "看人类伙伴——自动用手机前/后摄和PC摄像头融合分析她在做什么/环境"
    single_use = True

    parameters_schema = {
        "type": "object",
        "properties": {
            "analysis_prompt": {
                "type": "string",
                "description": "想重点观察什么",
                "default": "请全面描述画面"
            }
        },
        "required": []
    }

    def __init__(self, camera_tool=None, flash_llm_func=None,
                 mobile_relay=None, get_tools=None):
        self._camera_tool = camera_tool
        self._flash_llm = flash_llm_func
        self._mobile_relay = mobile_relay
        self._get_tools = get_tools or (lambda: {})
        self._mobile_disabled = False  # ping 失败后置 True，不销毁 relay 引用

    def get_user_facing_description(self, **kwargs) -> str:
        return "正在看人类伙伴..."

    async def execute(self, **kwargs) -> ToolResult:
        t0 = _time.time()
        analysis_prompt = kwargs.get("analysis_prompt", "请全面描述画面")
        self._mobile_disabled = False  # 每次执行重置标志
        viewing_intent = _derive_intent_rule(analysis_prompt)
        _log(f"[EYES:1] ═══ execute 入口 | prompt={analysis_prompt[:80]} | intent={viewing_intent[:40]} | relay={'SET' if self._mobile_relay else 'NONE'} | camera={'SET' if self._camera_tool else 'NONE'} ═══")

        sources: List[Dict[str, Any]] = []
        errors: List[str] = []

        # ── 0. Relay 连通性检查 ──
        if self._mobile_relay:
            _log("[EYES:0] 🔍 Relay ping (mobile_battery)...")
            try:
                import uuid
                ping = await asyncio.wait_for(
                    self._mobile_relay(str(uuid.uuid4()), "mobile_battery", {}),
                    timeout=20.0
                )
                _log(f"[EYES:0] ✅ Relay 通: {str(ping)[:150]}")
            except asyncio.TimeoutError:
                _log("[EYES:0] ❌ Relay ping 超时 — 手机WS断开！跳过手机源")
                self._mobile_disabled = True
            except Exception as e:
                _log(f"[EYES:0] ❌ Relay ping 异常: {e} — 跳过手机源")
                self._mobile_disabled = True

        # ── 1. 手机后摄 + PC 摄像头并行发起 ──
        _log("[EYES:2] ═══ 并行阶段: 后摄 + PC 摄像头 ═══")
        mobile_back_task = None
        if not self._mobile_disabled:
            mobile_back_task = asyncio.create_task(self._try_mobile_camera("back", analysis_prompt))
        pc_task = asyncio.create_task(self._try_pc_camera(analysis_prompt))

        # 收手机后摄
        if mobile_back_task:
            t1 = _time.time()
            result = await mobile_back_task
            dt1 = _time.time() - t1
            _log(f"[EYES:2] ├─ ① 手机后摄 完成 ({dt1:.1f}s) | success={result.get('success') if result else 'None'}")
            self._collect_result(result, "mobile_back", "手机后摄", sources, errors)

        # ── 2. 手机前摄（后摄完成后发起，硬件共享镜头）──
        if not self._mobile_disabled:
            _log("[EYES:2] ├─ ② 手机前摄 开始...")
            t2 = _time.time()
            result = await self._try_mobile_camera("front", analysis_prompt)
            dt2 = _time.time() - t2
            _log(f"[EYES:2] ├─ ② 手机前摄 完成 ({dt2:.1f}s) | success={result.get('success') if result else 'None'}")
            self._collect_result(result, "mobile_front", "手机前摄", sources, errors)

        # ── 3. 收 PC 摄像头 ──
        try:
            pc_result = await asyncio.wait_for(pc_task, timeout=_PC_SOURCE_TIMEOUT)
            _log(f"[EYES:2] └─ ④ PC 摄像头 完成 | success={pc_result.get('success') if pc_result else 'None'}")
            self._collect_result(pc_result, "pc_camera", "电脑摄像头", sources, errors)
        except asyncio.TimeoutError:
            _log("[EYES:2] └─ ④ PC 摄像头 超时")
            errors.append("🖥️ PC 摄像头: 超时")
        except Exception as e:
            _log(f"[EYES:2] └─ ④ PC 摄像头 异常: {e}")
            errors.append(f"🖥️ PC 摄像头: {e}")

        dt_total = _time.time() - t0
        _log(f"[EYES:1] ═══ 采集阶段完成 ({dt_total:.1f}s) | 成功={len(sources)} 失败={len(errors)} ═══")
        for src in sources:
            _log(f"[EYES:1]   ✅ {src['label']}: 已取得原图")
        for err in errors:
            _log(f"[EYES:1]   ❌ {err}")

        if not sources:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="现在看不到人类伙伴——手机和电脑的摄像头都没能获取到画面。",
                error="; ".join(errors),
            )

        # 这里只报告真实采集结果；同一轮 Flash Vision 负责多图分析、融合与自然回复。
        fused = f"已取得 {len(sources)} 路摄像头画面，等待统一视觉分析。"
        if errors:
            fused += f" 未取得：{'；'.join(errors)}。"
        images_meta = [
            {"source_key": s["source"], "label": s["label"], "image_url": s["image_url"],
             **({"attachment": s["attachment"]} if s.get("attachment") else {})}
            for s in sources if s.get("image_url")
        ]
        return ToolResult(status=ToolStatus.SUCCESS, content=fused,
                        extra_data={
                            "sources": sources,
                            "errors": errors,
                            "images": images_meta,
                            "analysis_prompt": analysis_prompt,
                            "viewing_intent": viewing_intent,
                        })

    @staticmethod
    def _collect_result(result, source_key: str, label: str,
                        sources: List[Dict], errors: List[str]):
        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")
        elif result and result.get("success"):
            sources.append({
                "source": source_key,
                "label": label,
                "analysis": {},
                "confidence": "pending",
                **({"image_url": result["image_url"]} if result.get("image_url") else {}),
                **({"attachment": result["attachment"]} if result.get("attachment") else {}),
            })
        elif result:
            errors.append(f"{label}: {result.get('error', '未知错误')}")
        else:
            errors.append(f"{label}: 无返回")

    # ─── 各路采集 ──────────────────────────────────────

    async def _try_mobile_camera(self, facing: str, prompt: str) -> Optional[Dict]:
        src = f"mobile_camera({facing})"
        if self._mobile_disabled or not self._mobile_relay:
            _log(f"[EYES:2]   ⏭️ {src} 跳过 (disabled={self._mobile_disabled}, relay={'SET' if self._mobile_relay else 'NONE'})")
            return {"success": False, "error": "手机未连接"}
        _log(f"[EYES:2]   📡 {src} → relay 调用...")

        t0 = _time.time()
        try:
            import uuid
            result_data = await asyncio.wait_for(
                self._mobile_relay(str(uuid.uuid4()), "mobile_camera", {"facing": facing}),
                timeout=_SOURCE_TIMEOUT
            )
            dt = _time.time() - t0
        except asyncio.TimeoutError:
            dt = _time.time() - t0
            _log(f"[EYES:2]   ⏰ {src} relay 超时 ({dt:.1f}s)")
            return {"success": False, "error": f"手机{facing}摄超时({dt:.1f}s)"}
        except Exception as e:
            dt = _time.time() - t0
            _log(f"[EYES:2]   ❌ {src} relay 异常 ({dt:.1f}s): {e}")
            return {"success": False, "error": f"手机{facing}摄异常: {e}"}

        _log(f"[EYES:2]   📥 {src} relay 返回 ({dt:.1f}s) | type={type(result_data).__name__}")

        if not isinstance(result_data, dict):
            _log(f"[EYES:2]   ❌ {src} 返回非dict: {type(result_data).__name__}")
            return {"success": False, "error": "手机返回异常数据类型"}

        success = result_data.get("success")
        content = result_data.get("content", "")
        has_image = bool(result_data.get("image_base64") or (result_data.get("extra_data") or {}).get("image_base64"))
        _log(f"[EYES:2]   📋 {src} result: success={success} | content={content[:80]} | has_image={has_image}")

        if not success:
            return {"success": False, "error": content or "手机返回失败"}

        extra = result_data.get("extra_data") or {}
        image_b64 = extra.get("image_base64") or result_data.get("image_base64")
        if not image_b64:
            return {"success": False, "error": "手机未返回图片"}

        attachment = None
        try:
            import base64 as _base64
            from .capture_storage import save_and_get_path, attachment_for_path
            file_path = save_and_get_path(
                f"mobile_{facing}", _base64.b64decode(image_b64), extension="jpg",
            )
            attachment = attachment_for_path(file_path, "image/jpeg")
        except Exception as save_error:
            _log(f"[EYES:2]   ⚠️ {src} 原图保存失败: {save_error}")

        _log(f"[EYES:3] 🖼️ {src} 原图已取得 (image: {len(image_b64)} chars)，等待统一视觉轮")
        return {
            "success": True,
            "image_url": f"data:image/jpeg;base64,{image_b64}",
            **({"attachment": attachment} if attachment else {}),
        }

    async def _try_pc_camera(self, prompt: str) -> Optional[Dict]:
        _log("[EYES:2]   🖥️ PC 摄像头 尝试...")
        camera_tool = self._camera_tool
        if not camera_tool:
            tools = self._get_tools()
            camera_tool = tools.get("capture_camera")
        if not camera_tool:
            _log("[EYES:2]   ❌ PC 摄像头 未注册")
            return {"success": False, "error": "PC 摄像头工具未注册"}

        t0 = _time.time()
        try:
            result = await asyncio.wait_for(
                camera_tool.execute(analysis_prompt=prompt, capture_only=True),
                timeout=_PC_SOURCE_TIMEOUT
            )
            dt = _time.time() - t0
        except asyncio.TimeoutError:
            dt = _time.time() - t0
            _log(f"[EYES:2]   ⏰ PC 摄像头 超时 ({dt:.1f}s)")
            return {"success": False, "error": "PC 摄像头超时"}
        except Exception as e:
            dt = _time.time() - t0
            _log(f"[EYES:2]   ❌ PC 摄像头 异常 ({dt:.1f}s): {e}")
            return {"success": False, "error": f"PC 摄像头: {e}"}

        _log(f"[EYES:2]   📥 PC 摄像头 返回 ({dt:.1f}s) | status={result.status}")

        if result.status == ToolStatus.ERROR:
            return {"success": False, "error": result.error or "PC 摄像头失败"}

        # PC 摄像头原始图（camera_tool extra_data）随结果返回，供 flash-vision 一条龙看图
        _img_b64 = None
        _extra = {}
        try:
            _extra = result.extra_data or {}
            _img_b64 = _extra.get("image_base64")
        except Exception:
            _img_b64 = None
        return {
            "success": True,
            **({"image_url": f"data:image/jpeg;base64,{_img_b64}"} if _img_b64 else {}),
            **({"attachment": _extra["attachment"]} if _extra.get("attachment") else {}),
        }
