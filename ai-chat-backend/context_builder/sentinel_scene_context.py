"""Complete, read-only active-day scene material for Sentinel's first judgment.

The shared reader owns boundaries, source validation and raw fallback. This
consumer only renders their chronological projection; it never closes or
digests a box, nor drops material to meet a character budget.
"""
from __future__ import annotations

import logging
from .active_day_scene_context import read_active_day_scene_material, render_active_day_scene_context

logger = logging.getLogger(__name__)


def render_sentinel_scene_context(material):
    """Render every valid closed summary and every open/fallback raw message."""
    return render_active_day_scene_context(material, speaker="AI")


async def build_sentinel_scene_context(session_id: str) -> str:
    return (await build_sentinel_scene_package(session_id))['text']


async def build_sentinel_scene_package(session_id: str) -> dict:
    if not session_id:
        return {'text': '', 'evidence_index': {}}
    try:
        material = await read_active_day_scene_material(session_id)
        evidence_index = {}
        text = render_active_day_scene_context(material, speaker='AI', evidence_index=evidence_index)
        try:
            from open_loops.progress import append_progress_evidence
            progress = append_progress_evidence(evidence_index)
        except Exception as exc:
            logger.warning('Sentinel progress context unavailable: %s', type(exc).__name__)
            progress = ''
        if progress:
            text += '\n\n' + progress
        logger.info("Sentinel scene context assembled (full): active_date=%s boxes=%d sources=%d chars=%d",
                    material["active_date"], len(material["boxes"]),
                    material.get("source_count", 0), len(text))
        return {'text': text, 'evidence_index': evidence_index}
    except Exception as exc:
        # No private bodies, source IDs, prompts or database paths in logs.
        logger.warning("Sentinel scene context unavailable: %s", type(exc).__name__)
        return {'text': '[当前活跃日盒子材料暂不可读取；本次判断没有带入对话材料。]',
                'evidence_index': {}}
