"""Rebuildable pending encoding cache, not a second event authority."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .encoding import ParsedEncoding
from .models import BatchSourceRef, EventDraft, SourceRef


def cache_path(source):
    return Path(source.db_path).parent / "scene_digests.db"


class SceneDigestStore:
    def __init__(self, path, *, create=False):
        self.path = Path(path).resolve()
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS scene_encodings (
                        batch_id TEXT PRIMARY KEY, source_digest TEXT NOT NULL,
                        sources TEXT NOT NULL, context_sources TEXT NOT NULL,
                        events TEXT NOT NULL, report TEXT NOT NULL,
                        box_id TEXT NOT NULL, active_date TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS scene_attempts (
                        batch_id TEXT PRIMARY KEY, source_digest TEXT NOT NULL,
                        attempts INTEGER NOT NULL, retry_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS day_scene_groups (
                        active_date TEXT PRIMARY KEY, settlement_id TEXT NOT NULL,
                        source_digest TEXT NOT NULL, sources TEXT NOT NULL, items TEXT NOT NULL);
                """)

    def load(self, plan, source):
        try:
            return self._load(plan, source)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            # A corrupt rebuildable cache cannot block canonical day ingestion.
            return None

    def _load(self, plan, source):
        if not self.path.is_file():
            return None
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM scene_encodings WHERE batch_id=?", (plan.batch.batch_id,)).fetchone()
        if not row or row["source_digest"] != plan.batch.source_digest:
            return None
        refs = tuple(BatchSourceRef(**item) for item in json.loads(row["sources"]))
        context = tuple(BatchSourceRef(**item) for item in json.loads(row["context_sources"]))
        if refs != plan.batch_sources or not source.validate_batch(plan.batch, batch_sources=refs):
            return None
        if context and not source.validate_batch_sources(context):
            return None
        events = []
        payload = json.loads(row["events"])
        if payload.get('source_updates'):
            return None  # Pending drafts are not a complete scene encoding.
        for item in payload["events"]:
            item["sources"] = tuple(SourceRef(**ref) for ref in item["sources"])
            events.append(EventDraft(**item))
        if events and not source.validate_refs(tuple(ref for event in events for ref in event.sources)):
            return None
        return ParsedEncoding(**{**payload, "events": tuple(events)})

    def save(self, box, plan, parsed, report, source, *, context_sources=()):
        if not box.closed or not source.validate_batch(plan.batch, batch_sources=plan.batch_sources):
            raise ValueError("scene_sources_changed")
        if context_sources and not source.validate_batch_sources(context_sources):
            raise ValueError("scene_context_changed")
        if parsed.overflow or parsed.source_updates:
            raise ValueError("scene_encoding_incomplete")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO scene_encodings VALUES(?,?,?,?,?,?,?,?)", (
                plan.batch.batch_id, plan.batch.source_digest,
                json.dumps([asdict(ref) for ref in plan.batch_sources]),
                json.dumps([asdict(ref) for ref in context_sources]),
                json.dumps(asdict(parsed), ensure_ascii=False),
                json.dumps(report), box.box_id, box.active_date,
            ))

    def claim(self, plan, *, now, context_sources=()):
        context_digest = hashlib.sha256(json.dumps([asdict(ref) for ref in context_sources], sort_keys=True).encode()).hexdigest()
        revision = plan.batch.source_digest + ":" + context_digest
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT source_digest,attempts,retry_at FROM scene_attempts WHERE batch_id=?",
                             (plan.batch.batch_id,)).fetchone()
            attempts = old[1] if old and old[0] == revision else 0
            if old and old[0] == revision and (attempts >= 3 or old[2] > now):
                return False
            db.execute("INSERT OR REPLACE INTO scene_attempts VALUES(?,?,?,?)",
                       (plan.batch.batch_id, revision, attempts + 1, now + 3600))
            return True

    def projection(self, planner, active_date, *, completed_before=""):
        """Only currently closed, completely encoded boxes can replace raw text."""
        items = []
        complete = True
        for box in planner.boxes_for_day(active_date, completed_before=completed_before):
            if not box.closed:
                complete = False
                continue
            encodings = [self.load(batch, planner.source) for batch in box.batches]
            if any(encoding is None for encoding in encodings):
                complete = False
                continue
            for batch, encoding in zip(box.batches, encodings):
                from .day_projection import render_k_facing_summary
                # Atomic summaries are provisional subscene impressions. The day
                # settlement is responsible for later semantic regrouping.
                from .state_changes import validated_changes, render
                current = {m.message_id: m for m in planner.source.read_batch(batch.batch_sources)}
                summaries = []
                changes_valid = True
                for event in encoding.events:
                    changes = event.attributes.get('state_changes', [])
                    valid = validated_changes(changes, current, {ref.message_id for ref in event.sources})
                    if len(valid) != len(changes):
                        changes_valid = False
                        break
                    # A valid progress field can contain the only preserved
                    # correction/outcome. Scene replacement must include it.
                    prose = '\n'.join(part for part in (render(valid), event.summary) if part)
                    summaries.append(render_k_facing_summary(prose))
                if not changes_valid:
                    complete = False
                    continue
                refs = batch.batch_sources
                items.append({"order": len(items), "box_id": box.box_id,
                              "summary": "；".join(summaries),
                              "message_ids": [ref.message_id for ref in refs],
                              "start_ts": refs[0].source_ts, "end_ts": refs[-1].source_ts,
                              "vividness": max((event.importance + abs(event.emotional_weight)
                                                for event in encoding.events), default=0.0),
                              "source_count": len(refs), "stage": "provisional"})
        return {"active_date": active_date, "items": items, "complete": complete}

    def save_scene_groups(self, active_date, settlement_id, refs, items, source):
        if not source.validate_batch_sources(refs):
            raise ValueError("scene_group_source_changed")
        from .models import digest_batch_sources
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO day_scene_groups VALUES(?,?,?,?,?)", (
                active_date, settlement_id, digest_batch_sources(refs),
                json.dumps([asdict(ref) for ref in refs]), json.dumps(items, ensure_ascii=False)))

    def load_scene_groups(self, active_date, settlement_id, refs, source):
        if not self.path.is_file() or not settlement_id:
            return None
        try:
            from .models import digest_batch_sources
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT settlement_id,source_digest,sources,items FROM day_scene_groups "
                                 "WHERE active_date=?", (active_date,)).fetchone()
            if not row or row[0] != settlement_id or row[1] != digest_batch_sources(refs):
                return None
            frozen = tuple(BatchSourceRef(**ref) for ref in json.loads(row[2]))
            if frozen != refs or not source.validate_batch_sources(frozen):
                return None
            items = json.loads(row[3])
            allowed = {ref.message_id for ref in refs}
            if (not isinstance(items, list) or not items or any(not isinstance(item, dict)
                or not isinstance(item.get("summary"), str) or not item.get("message_ids")
                or not set(item["message_ids"]).issubset(allowed) for item in items)):
                return None
            return items
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return None
