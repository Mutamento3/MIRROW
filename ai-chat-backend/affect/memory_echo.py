"""Read the feelings AI actually authored for the memories shown this turn.

This is a transient source association, not a self-report or reinforcement.
The current Affect ledger and its lifecycle always remain authoritative.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from .service import AffectService, ACTIVE_CUTOFF, _redact_prompt_text, _renderer_text
from .store import DEFAULT_SETTINGS


@dataclass(frozen=True)
class MemoryFeelingSource:
    event_id: str
    date: str
    source_type: str
    source_id: str
    # A conversation source is checked against the authoritative raw words.
    content: str = ""


@dataclass(frozen=True)
class MemoryEcho:
    text: str = ""
    count: int = 0
    status: str = "no_link"

    def safe_observation(self):
        return {"status": self.status, "count": self.count, "chars": len(self.text)}


def read_memory_echo(path, sources, *, now="", max_chars=480, max_items=3):
    """No writes, schema initialization, LLM calls, or reset of old intensity."""
    if not sources or max_chars < 80:
        return MemoryEcho()
    path = Path(path).resolve()
    if not path.is_file():
        return MemoryEcho(status="unavailable")
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            settings = dict(DEFAULT_SETTINGS)
            settings.update({r['key']: json.loads(r['value']) for r in db.execute('SELECT key,value FROM affect_meta')})
            if settings.get('engine_mode') != 'enabled' or not settings.get('self_context_enabled', True):
                return MemoryEcho(status="disabled")
            by_key = {}
            for source in sources:
                by_key.setdefault((source.source_type, source.source_id), source)
            ids = tuple(dict.fromkeys(s.source_id for s in sources))
            if len(ids) > 400:
                return MemoryEcho(status="source_budget")
            marks = ','.join('?' for _ in ids)
            # Only accepted authored feelings. Later reappraisal/correction is
            # a semantic boundary; an old snapshot cannot bypass it.
            candidates = db.execute(f"""
                SELECT t.*, c.source_refs_json AS current_refs,
                    c.initial_intensity AS current_intensity, c.half_life_seconds AS current_half_life,
                    c.last_reinforced_at AS current_reinforced_at, c.started_at AS current_started_at
                FROM affect_transitions t
                JOIN affect_components c ON c.component_id=t.component_id
                WHERE t.owner='k_self' AND c.owner='k_self' AND c.status='active'
                  AND c.authorship IN ('k_self_report','k_activity_settlement')
                  AND t.status='accepted' AND t.source_invalidated=0
                  AND t.operation IN ('add','reinforce','reappraise')
                  AND EXISTS (SELECT 1 FROM json_each(t.source_refs_json) r
                              WHERE json_extract(r.value,'$.source_id') IN ({marks}))
                  AND NOT EXISTS (SELECT 1 FROM affect_transitions newer
                      WHERE newer.component_id=t.component_id AND newer.rowid>t.rowid
                        AND newer.status='accepted'
                        AND newer.operation IN ('reappraise','correct','resolve'))
                ORDER BY t.rowid DESC""", ids).fetchall()
            revision_cache = {}

            def valid(ref):
                key = (ref.get('source_type'), ref.get('source_id'), ref.get('source_revision'))
                if type(key[2]) is not int or key[2] < 1:
                    return None
                if key not in revision_cache:
                    row = db.execute('SELECT * FROM affect_source_revisions WHERE source_type=? AND source_id=? '
                                     'ORDER BY source_revision DESC LIMIT 1', key[:2]).fetchone()
                    revision_cache[key] = dict(row) if row else {}
                row = revision_cache[key]
                if row.get('status') != 'active' or row.get('source_revision') != key[2]:
                    return None
                return row

            lines, used = [], set()
            for transition in candidates:
                cid = transition['component_id']
                if cid in used:
                    continue
                current_roots = [r for r in json.loads(transition['current_refs'])
                                 if r.get('relation') in {'causal', 'self_report'}]
                if not current_roots or not all(valid(r) for r in current_roots):
                    continue
                refs = json.loads(transition['source_refs_json'])
                roots = [r for r in refs if r.get('relation') in {'causal','self_report'}]
                if not roots or not all(valid(r) for r in roots):
                    continue
                matched = None
                for ref in roots:
                    key = (ref.get('source_type'), ref.get('source_id'))
                    if ref.get('relation') != 'self_report' or key not in by_key:
                        continue
                    row = valid(ref)
                    source = by_key[key]
                    if row.get('owner') != 'k':
                        continue
                    if key[0] == 'conversation_message':
                        digest = hashlib.sha256(_redact_prompt_text(source.content, 2000).encode('utf-8', 'replace')).hexdigest()
                        if row.get('role') != 'assistant' or digest != row.get('content_hash'):
                            continue
                    elif key[0] == 'wander_settlement':
                        if row.get('role') != 'wander_settlement' or json.loads(row['metadata_json']).get('settled') is not True:
                            continue
                    else:
                        continue
                    matched = source
                    break
                if matched is None:
                    continue
                state = json.loads(transition['after_json'])
                label = _renderer_text(_redact_prompt_text(state.get('label'), 80), 80)
                reason = _renderer_text(_redact_prompt_text(state.get('reason'), 160), 160)
                if not label or not reason:
                    continue
                # The historical feeling can be remembered even after natural
                # decay. Its historical intensity is never projected as current.
                from .reported_state import reported_state
                current_row = db.execute('SELECT * FROM affect_components WHERE component_id=?', (cid,)).fetchone()
                current = reported_state(db, Path(path).parent, dict(current_row),
                    normalize_label=AffectService._normalized_component_label, parse_time=AffectService._v2_now)
                effective = AffectService._effective_intensity(current, now)
                tail = ('；按原有衰减仍留有' + AffectService._intensity_word(effective) + '余韵'
                        if effective >= ACTIVE_CUTOFF else '；这是当时留下的感受')
                parsed_day = date.fromisoformat(matched.date[:10])
                day = f'{parsed_day.year}年{parsed_day.month}月{parsed_day.day}日'
                line = f'- 随{day}这段记忆一并想起，你当时留下的感受是“{label}”，写下：“{reason}”{tail}。'
                if len('\n'.join(['[回忆带回的感受]', *lines, line])) > max_chars:
                    continue
                lines.append(line)
                used.add(cid)
                if len(lines) >= max_items:
                    break
            if not lines:
                return MemoryEcho()
            return MemoryEcho('\n'.join(['[回忆带回的感受]', *lines]), len(lines), 'linked')
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return MemoryEcho(status="unavailable")
