"""Music execution facts shared by runtime, event cards and frozen memory.

Node receipts are authoritative; AI's reflections remain attributed prose and
are never parsed to infer playback. This module neither executes nor settles.
"""
from __future__ import annotations


MUSIC_EXPRESSION_CONTRACT = (
    "\n音乐感想来自这次听歌经历及收到的歌曲、旋律与歌词材料。"
    "听歌进度以 music_execution 记录为准；AI 当时原述保留为当时的主观表达。"
)


def music_execution(node: dict) -> dict:
    payload = node.get("source_payload") or {}
    payload = payload if isinstance(payload, dict) else {}
    signal = str(node.get("completion_signal") or "")
    status = str(node.get("execution_status") or "unknown")
    experience = payload.get('listening_experience') or {}
    if experience.get('version') == 1:
        complete = status == 'succeeded' and signal == 'listening_finished'
        waiting = status == 'waiting_external' and node.get('state') not in {'aborted', 'completed'}
        kind = 'listening_completed' if complete else 'listening' if waiting else 'listening_interrupted'
        return dict(node_id=node.get('node_id'), execution_status=status, completion_signal=signal,
                    experience_kind=kind,
                    fact='这首歌已听完。' if complete else '正在听这首歌。' if waiting else '这次听歌尚未完整结束。',
                    duration_sec=experience.get('duration_sec'), started_at=experience.get('started_at'),
                    completes_at=experience.get('completes_at'))
    kind, text = "unconfirmed", "本节点未取得音乐分析或播放完成回执。"
    if status == "succeeded":
        if signal == "analysis_complete":
            kind = "material_analysis"
            text = "本节点完成的是歌曲资料分析；未取得实际播放完成回执。"
            if payload.get("played") is False and payload.get("playback_requested") is False:
                text = "本节点完成的是歌曲资料分析；本节点未执行实际播放。"
        elif signal == "playback_finished":
            kind, text = "playback_completed", "本节点已收到实际播放完成回执。"
    if (kind == "unconfirmed" and status in {"succeeded", "waiting_external", "interrupted", "failed"}
            and (node.get("side_effect_refs") or {}).get("playback_started") is True):
        kind, text = "playback_started", "本节点已确认播放启动；未取得播放完成回执。"
    return {
        "node_id": node.get("node_id"), "execution_status": status,
        "completion_signal": signal, "experience_kind": kind, "fact": text,
        **{key: payload[key] for key in ("played", "playback_requested", "playback_target", "analysis_source")
           if key in payload},
    }
