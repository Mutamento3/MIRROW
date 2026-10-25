"""话题结束后的日期归属与日记补全编排。

这里不持有数据库或模型客户端，只接收依赖并返回真实结果，避免 main.py
继续膨胀，也避免失败被 fire-and-forget 包装误记为成功。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

class DiaryBackfillError(RuntimeError):
    """至少一个有真实对话的日期未能生成日记。"""

    def __init__(self, failed_dates: list[str], generated: int, stale_dates: list[str] | None=None):
        self.failed_dates = failed_dates
        self.stale_dates = stale_dates or []
        self.generated = generated
        super().__init__(f"日记补全部分失败: {', '.join(failed_dates)}" + (f"；已有日记原料更新，需复核: {', '.join(self.stale_dates)}" if self.stale_dates else '') + f'（已成功 {generated} 篇）')

def resolve_topic_date(start_time: str, fallback_now: Optional[datetime]=None) -> str:
    """以话题开始时间的北京时间日期作为话题级后处理日期。"""
    now = fallback_now or datetime.now()
    if start_time:
        try:
            raw = start_time.strip()
            if raw.endswith('Z'):
                raw = raw[:-1] + '+00:00'
            parsed = datetime.fromisoformat(raw)
            beijing = timezone(timedelta(hours=8))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=beijing)
            return parsed.astimezone(beijing).strftime('%Y-%m-%d')
        except Exception:
            pass
    return now.strftime('%Y-%m-%d')

def format_topic_digest_line(index: int, message: dict) -> str:
    """Keep event experience, AI expression and Human speech distinct in legacy digest."""
    content = str(message.get('content') or '')
    event_type = str(message.get('event_type') or '')
    role = str(message.get('role') or '')
    if role == 'system' and event_type == 'wander_activity':
        return f'[MSG {index}] AI 的漫想结算（自己的经历与感受，未必对人类伙伴分享）: {content}'
    if role == 'system':
        return f'[MSG {index}] 系统事实: {content}'
    if role == 'user' and event_type == 'ui_action:visit_workshop':
        from workshop.ui_action import invitation_fact as workshop_invitation_fact
        return f'[MSG {index}] 系统事实: {workshop_invitation_fact(content)}'
    if role == 'user' and event_type == 'ui_action:play_game':
        from wander_manager.game_invitation import invitation_fact as game_invitation_fact
        return f'[MSG {index}] 系统事实: {game_invitation_fact(content)}'
    speaker = '人类伙伴' if role == 'user' else 'AI'
    invitation_label = ''
    return f'[MSG {index}] {speaker}{invitation_label}: {content}'

async def backfill_missing_diaries(*, chronicle, generate_diary: Callable[..., Awaitable[object]], flash_llm_func, build_sentinel_summary: Optional[Callable[[str], Awaitable[str]]]=None, max_days: int=30, now: Optional[datetime]=None, logger=None, on_review_required: Optional[Callable[[list[str]], None]]=None, on_date_completed: Optional[Callable[[str], None]]=None) -> int:
    """补全最近日期，并把模型失败作为真实失败抛给任务追踪层。"""
    current = now or datetime.now()
    cursor = current - timedelta(days=max_days)
    end = datetime.strptime(current.strftime('%Y-%m-%d'), '%Y-%m-%d')
    cursor = datetime.strptime(cursor.strftime('%Y-%m-%d'), '%Y-%m-%d')
    generated = 0
    failed_dates: list[str] = []
    stale_dates: list[str] = []
    while cursor < end:
        date_str = cursor.strftime('%Y-%m-%d')
        try:
            completed = False
            existing = chronicle.get_diary_entry_by_date(date_str)
            if existing is not None:
                from open_loops.day_review import resume_saved
                resume_saved(existing)
                from event_chronicle import diary_source_status
                if diary_source_status(existing, date_str) == 'stale':
                    stale_dates.append(date_str)
                else:
                    completed = True
            else:
                if logger:
                    logger.info('补生成日记: %s', date_str)
                from daily_close import ensure_diary
                entry, created = await ensure_diary(date_str, chronicle, generate_diary, flash_llm_func, build_sentinel_summary)
                generated += int(created)
                completed = entry is not None
            if completed and on_date_completed is not None:
                on_date_completed(date_str)
        except Exception:
            failed_dates.append(date_str)
            if logger:
                logger.exception('日记补全失败，继续处理其他日期: %s', date_str)
        cursor += timedelta(days=1)
    if logger and generated:
        logger.info('日记补全完成: 生成 %s 篇', generated)
    if stale_dates:
        if logger:
            logger.warning('已有日记原料更新，保留原日记待复核: %s', ', '.join(stale_dates))
        if on_review_required is not None:
            on_review_required(stale_dates)
    if failed_dates:
        raise DiaryBackfillError(failed_dates, generated, stale_dates)
    return generated
