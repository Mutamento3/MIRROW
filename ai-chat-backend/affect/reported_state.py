"""Read current AI-authored strength from its exact accepted source, without writes."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3

from .feeling_contract import normalize_wander_feelings
from .sidecar import OPEN, CLOSE, parse_affect_sidecar

HALF_LIVES = {'momentary': 1200, 'situational': 10800, 'lingering': 64800}


def _refs(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def reported_state(db, folder, component, *, normalize_label, parse_time):
    """Only a matching current branch head can calibrate a disposable read model.

    Missing legacy material and manual corrections retain their stored state.
    This never searches backwards past a newer branch or refreshes its clock.
    """
    original = dict(component)
    if (component.get('owner') != 'k_self'
            or component.get('authorship') not in {'k_self_report', 'k_activity_settlement'}
            or not component.get('parent_transition_id')):
        return original
    try:
        head = db.execute('SELECT * FROM affect_transitions WHERE transition_id=?',
                          (component['parent_transition_id'],)).fetchone()
        if (not head or head['component_id'] != component.get('component_id')
                or head['owner'] != 'k_self' or head['status'] != 'accepted'
                or head['source_invalidated'] or head['operation'] not in {'add','reinforce','reappraise'}):
            return original
        roots = [ref for ref in _refs(head['source_refs_json']) if ref.get('relation') == 'self_report']
        if len(roots) != 1:
            return original
        ref = roots[0]
        if ref not in _refs(component.get('source_refs', component.get('source_refs_json'))):
            return original
        source_type, source_id, revision = ref['source_type'], ref['source_id'], ref['source_revision']
        source = db.execute('SELECT * FROM affect_source_revisions WHERE source_type=? AND source_id=? AND source_revision=?',
                            (source_type, source_id, revision)).fetchone()
        if not source or source['status'] != 'active' or source['owner'] != 'k':
            return original
        when = source['occurred_at']
        if source_type == 'conversation_message' and source['role'] == 'assistant':
            rows = db.execute("SELECT payload_json FROM affect_episode_observations WHERE source_type=? AND source_id=? "
                              "AND source_revision=? AND owner='k_self' AND status='accepted' LIMIT 2",
                              (source_type, source_id, revision)).fetchall()
            if len(rows) != 1:
                return original
            raw = json.loads(rows[0]['payload_json'])
            allowed = {'operation','feeling','family','reason','intensity','persistence','target'}
            bound = {'_bound_assistant_source_id','_bound_user_source_id','_bound_user_source_revision'}
            if set(raw) - allowed - bound or raw.get('_bound_assistant_source_id', source_id) != source_id:
                return original
            payload = {key: raw[key] for key in allowed if key in raw}
            parsed = parse_affect_sidecar(OPEN + json.dumps(payload, ensure_ascii=False) + CLOSE)
            if not parsed.accepted:
                return original
            report = parsed.payload
        elif source_type == 'wander_settlement' and source['role'] == 'wander_settlement':
            if json.loads(source['metadata_json']).get('settled') is not True:
                return original
            path = Path(folder) / 'wander_runtime.db'
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as runtime:
                runtime.execute('PRAGMA query_only=ON')
                row = runtime.execute('SELECT state,settlement_reason,ended_at,emotion_effect FROM wander_activities WHERE activity_id=?',
                                      (source_id,)).fetchone()
            if (not row or row[0] not in {'completed','interrupted','aborted'} or not row[1]
                    or row[1] == 'execution_error' or row[1].startswith('not_started')):
                return original
            effect = json.loads(row[3])
            normalized = normalize_wander_feelings(effect) if effect.get('changed') is True else None
            if not normalized or parse_time(row[2]) != parse_time(when):
                return original
            report = dict(normalized, feeling=normalized['to_emotion'], reason=normalized['delta'])
        else:
            return original
        if (not when or not component.get('last_reinforced_at')
                or parse_time(when) != parse_time(component['last_reinforced_at'])
                or normalize_label(report['feeling']) != normalize_label(component.get('label'))
                or str(report['reason']).strip() != str(component.get('reason') or '').strip()):
            return original
        result = dict(original)
        result.update(initial_intensity=report['intensity'], persistence_class=report['persistence'],
                      half_life_seconds=HALF_LIVES[report['persistence']])
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError):
        return original
