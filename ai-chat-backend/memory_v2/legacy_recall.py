"""Legacy impressions in V2's one ranker, without recreating old events."""

from datetime import date
from pathlib import Path

from behavior_scheduler.bookshelf_search import sanitize_archive_text

from .legacy_archive import LegacyMemoryArchive, get_legacy_archive
from .recall import EventRecallIndex, RecallDocument


def _day(value) -> str:
    clean = str(value or "")[:10]
    try:
        date.fromisoformat(clean)
        return clean
    except ValueError:
        return ""


def _importance(value) -> float:
    try:
        return max(0, min(1, float(value) / 10))
    except (ValueError, TypeError):
        return 0.5


class LegacyArchiveRecallIndex(EventRecallIndex):
    def __init__(self, authority_path: str | Path, *, archive: LegacyMemoryArchive | None = None):
        super().__init__(authority_path)
        production_events = Path(__file__).resolve().parents[1] / "events"
        self.archive = archive or (get_legacy_archive()
            if Path(authority_path).resolve().parent == production_events.resolve() else None)

    def _load_documents(self) -> list[RecallDocument]:
        # A fresh install has no legacy archive; it still has a complete V2 path.
        if self.archive is None or not self.archive.root.is_dir():
            return []
        documents = []
        for item in self.archive.list_buckets():
            meta = item["metadata"]
            tags = meta.get("tags", [])
            tags = [str(tag) for tag in tags] if isinstance(tags, list) else [str(tags)]
            if (meta.get("todo") or meta.get("profile")
                    or meta.get("type") in {"profile", "todo", "collection"}
                    or set(tags).intersection({"user_profile", "todo", "collection"})):
                continue
            body = sanitize_archive_text(item["content"], limit=100_000)
            if not body:
                continue
            subject = str(meta.get("subject", "")).strip().casefold()
            subject = "k" if subject == "k" else (
                "human" if subject in {"human", "人类伙伴"} else "shared")
            start = _day(meta.get("source_date"))
            end = _day(meta.get("latest_source_date")) or start
            if start and end and start > end:
                start, end = end, start
            # Creation/update time is archive bookkeeping, not occurrence time.
            documents.append(RecallDocument(
                document_id=f"legacy:{item['id']}", event_ids=(), source_message_ids=(),
                summaries=(body,), subject_ids=(subject,), participant_ids=(subject,),
                event_types=(), facets=tuple(str(tag) for tag in tags),
                source_kinds=("legacy_summary",), active_date_from=start,
                active_date_to=end, date_from=start, date_to=end,
                importance=_importance(meta.get("importance", 5)),
                confidence=0.55, source_layer="legacy", display_text=body,
            ))
        return documents
