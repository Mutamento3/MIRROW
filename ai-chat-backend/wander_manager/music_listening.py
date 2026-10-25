"""Persisted song-length listening, independent of the optional speaker output.

Prepared material is private until the listening interval finishes. Recovery
only reads that checkpoint; it never selects, analyzes or starts output again.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import inspect
import math


def duration_seconds(value):
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


async def resolve_duration(payload):
    seconds = duration_seconds(payload.get('duration_sec'))
    if seconds is not None:
        return seconds, 'song_material.duration_sec'
    from music_system.service import get_service
    song = await get_service().provider.song(str(payload['netease_song_id']))
    if not isinstance(song, dict) or (song.get('id') is not None
            and str(song['id']) != str(payload['netease_song_id'])):
        return None, ''
    seconds = duration_seconds(song.get('duration_sec'))
    if seconds is not None:
        return seconds, 'song_detail.duration_sec'
    # The music provider's normalized Song.duration is milliseconds.
    milliseconds = duration_seconds(song.get('duration'))
    return (milliseconds / 1000, 'song_detail.duration_ms') if milliseconds else (None, '')


def is_listening(node):
    return (node.get('source_payload') or {}).get('listening_experience', {}).get('version') == 1


def material_for_k(payload):
    """New listening contract only; frozen legacy experience is not redrawn."""
    if (payload.get('listening_experience') or {}).get('version') != 1:
        return payload
    return {key: value for key, value in payload.items() if key not in {
        'played', 'playback_requested', 'playback_target', 'analysis_source',
        'playback_error', 'playback_receipt_state',
    }}


async def prepare(payload, *, handler, clock, checkpoint, interrupted,
                  playback_enabled, playback_target):
    from .node_execution_adapter import NodeExecutionResult, NodeExecutionStatus
    material = deepcopy(payload)
    try:
        seconds, source = await resolve_duration(material)
        start = clock()
        due = start + timedelta(seconds=seconds) if seconds is not None else None
    except Exception:
        seconds, source, due = None, '', None
    if due is None:
        return NodeExecutionResult(NodeExecutionStatus.FAILED,
            summary='这首歌尚未取得可靠时长，听歌未开始。', error='music_duration_unavailable',
            payload={key: material[key] for key in ('title', 'artist', 'netease_song_id', 'fingerprint') if key in material})
    material['duration_sec'] = seconds
    material['played'] = None if playback_enabled else False
    material['playback_requested'] = playback_enabled
    material['playback_target'] = playback_target if playback_enabled else 'none'
    experience = dict(version=1, state='listening', started_at=start.isoformat(),
                      completes_at=due.isoformat(), duration_sec=seconds, duration_source=source)
    material['listening_experience'] = experience
    visible = {key: deepcopy(material[key]) for key in (
        'title', 'artist', 'netease_song_id', 'fingerprint', 'duration_sec', 'listening_experience')}
    output = dict(requested=playback_enabled, target=material['playback_target'],
                  state='not_requested' if not playback_enabled else 'not_started')
    refs = dict(expected_completion_at=due.isoformat(),
                music_listening_material=material, device_playback=output)
    result = NodeExecutionResult(NodeExecutionStatus.WAITING_EXTERNAL,
        completion_signal='listening_started', summary=f"正在听《{material['title']}》，等待这首歌结束。",
        payload=visible, side_effect_refs=refs)
    # Save before optional device I/O. A crash during dispatch leaves an
    # auditable unknown attempt, never an invitation to replay that action.
    checkpoint(result)
    if not playback_enabled or interrupted():
        return result
    if playback_target not in {'computer', 'mobile'}:
        output.update(state='failed', error='invalid_playback_target')
        return result
    output['state'] = 'attempted'
    checkpoint(result)
    try:
        playback = handler.start_runtime_playback
        parameters = inspect.signature(playback).parameters
        kwargs = {}
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if accepts_kwargs or 'target' in parameters:
            kwargs['target'] = playback_target
        if accepts_kwargs or 'song_data' in parameters:
            kwargs['song_data'] = material
        started = await playback(material['netease_song_id'], **kwargs)
        receipt = getattr(handler, '_last_runtime_playback', None)
        info = receipt.get('playback', {}) if isinstance(receipt, dict) else {}
        output.update(state='started' if started else 'failed',
                      music_session_id=str(info.get('music_session_id') or ''))
        material['played'] = bool(started)
        if not started:
            output['error'] = 'playback_start_failed'
    except Exception:
        output.update(state='unknown', error='playback_dispatch_unconfirmed')
    return result


def poll(node, *, now, interrupted=False):
    from .node_execution_adapter import NodeExecutionResult, NodeExecutionStatus
    visible = node['source_payload']
    refs = node['side_effect_refs']
    if interrupted:
        return NodeExecutionResult(NodeExecutionStatus.SKIPPED, summary='这次听歌已中断。',
            payload=visible, side_effect_refs=refs, error='interrupt_requested')
    due = datetime.fromisoformat(visible['listening_experience']['completes_at'])
    if now < due:
        return NodeExecutionResult(NodeExecutionStatus.WAITING_EXTERNAL,
            completion_signal='listening_started', summary=node.get('source_summary') or '正在听歌。',
            payload=visible, side_effect_refs=refs)
    material = deepcopy(refs['music_listening_material'])
    material['listening_experience'].update(state='completed', completed_at=due.isoformat())
    completed_refs = {key: value for key, value in refs.items() if key != 'music_listening_material'}
    return NodeExecutionResult(NodeExecutionStatus.SUCCEEDED, completion_signal='listening_finished',
        summary=f"听完了《{material['title']}》。", payload=material, side_effect_refs=completed_refs)
