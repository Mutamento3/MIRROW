"""Active-day closing rules and shared diary generation exclusion."""
import asyncio
import weakref
from pathlib import Path
from datetime import timedelta

_locks = weakref.WeakKeyDictionary()


def resolve_target(trigger, topic_date, now, message_count, has_active_topics):
    if trigger not in {'manual', 'covenant'} or message_count < 10 or has_active_topics:
        return None
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    if topic_date == today and now.hour >= 18:
        return topic_date
    # An overnight close belongs to yesterday; arbitrary historical topics do
    # not turn into an automatic backlog of social posts.
    if topic_date == yesterday and now.hour < 6:
        return topic_date
    return None


def diary_lock(chronicle, date):
    loop_locks = _locks.setdefault(asyncio.get_running_loop(), {})
    db = str(Path(chronicle.db_path).resolve()) if getattr(chronicle, 'db_path', None) else id(chronicle)
    return loop_locks.setdefault((db, date), asyncio.Lock())


def needs_diary_close_work(entry):
    return entry is None or bool((getattr(entry, "structured_data", None) or {}).get("_open_loop_day_review"))


async def ensure_diary(date, chronicle, generate, llm, build_summary=None, *, review_open_loops=False):
    """Recheck inside the date lock, shared by backfill and explicit close."""
    async with diary_lock(chronicle, date):
        existing = chronicle.get_diary_entry_by_date(date)
        if existing is not None:
            from open_loops.day_review import resume_saved
            resume_saved(existing)
            return existing, False
        summary = await build_summary(date) if build_summary else ''
        options = {"review_open_loops": True} if review_open_loops else {}
        entry = await generate(date, chronicle, llm, summary or '', **options)
        if entry is None:
            raise RuntimeError(f'日记生成失败，等待补全: {date}')
        from open_loops.day_review import resume_saved
        resume_saved(entry)
        return entry, True
