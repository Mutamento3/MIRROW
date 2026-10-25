"""One durable identity for an incoming interaction and its reply attempt.

Notification signals are wakeups. Only a verified app read can bind the comment
that AI replies to. Opening a list never means AI has consumed its contents.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from xhs_publish.store import ROOT, now


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def reply_text(text):
    if (not isinstance(text, str) or not text.strip() or '\x00' in text
            or len(text.encode('utf-16-le')) // 2 > 500):
        raise ValueError('reply_requires_1_to_500_units')
    return text


def validate_target(target, incoming):
    if not isinstance(target, dict) or target.get('source_verified') is not True:
        raise ValueError('comment_target_unverified')
    required = ('identity', 'actor_name', 'comment_text', 'note_title', 'note_author')
    if any(not isinstance(target.get(k), str) or not target[k].strip() for k in required):
        raise ValueError('comment_target_incomplete')
    if target.get('source_ref') != incoming['source_ref']:
        raise ValueError('comment_source_changed')
    if target.get('identity_basis') not in {'native_comment_id', 'visible_note_and_exact_comment'}:
        raise ValueError('comment_identity_unavailable')
    if target.get('ambiguous'):
        raise ValueError('comment_identity_ambiguous')
    if target['actor_name'] != incoming['actor_name']:
        raise ValueError('comment_actor_changed')
    if not isinstance(target.get('note_body', ''), str) or not isinstance(target.get('thread', []), list):
        raise ValueError('comment_material_invalid')
    # Preserve native fields and source scope in the exact material AI receives.
    return deepcopy(target)


class InteractionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else ROOT / 'events' / 'xhs_interactions.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            from .images import TABLE_SQL
            db.execute(TABLE_SQL)
            db.execute('CREATE TABLE IF NOT EXISTS interactions '
                       '(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,account_id TEXT NOT NULL,'
                       'source_ref TEXT NOT NULL,record TEXT NOT NULL,UNIQUE(account_id,source_ref))')
            db.execute('CREATE TABLE IF NOT EXISTS replies '
                       '(id TEXT PRIMARY KEY,interaction_id TEXT NOT NULL,session_id TEXT NOT NULL,'
                       'state TEXT NOT NULL,record TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS reply_claims '
                       '(target_key TEXT PRIMARY KEY,reply_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS attention '
                       '(batch_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,state TEXT NOT NULL,record TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, interaction_id, session_id):
        with self.connect() as db:
            row = db.execute('SELECT record FROM interactions WHERE id=? AND session_id=?',
                             (interaction_id, session_id)).fetchone()
        return json.loads(row[0]) if row else None

    def ingest(self, session_id, account, item):
        if not session_id or not isinstance(item, dict):
            raise ValueError('interaction_identity_required')
        if item.get('kind') not in {'comment', 'mention'}:
            raise ValueError('unsupported_interaction_kind')
        if any(not isinstance(item.get(k), str) or not item[k].strip()
               for k in ('source_ref', 'actor_name', 'notification_text', 'observed_at')):
            raise ValueError('interaction_source_incomplete')
        identity = digest([account['account_id'], item['source_ref']])
        value = {'id': identity, 'session_id': session_id, 'account': deepcopy(account),
                 'source_ref': item['source_ref'], 'incoming': deepcopy(item),
                 'state': 'unread', 'created_at': now(), 'read_at': '', 'target': {},
                 'attention_state': 'pending'}
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO interactions VALUES (?,?,?,?,?)',
                       (identity, session_id, account['account_id'], item['source_ref'],
                        json.dumps(value, ensure_ascii=False)))
            row = db.execute('SELECT record FROM interactions WHERE id=?', (identity,)).fetchone()
        stored = json.loads(row[0])
        if stored['session_id'] != session_id or stored['account'] != account:
            raise ValueError('interaction_owner_changed')
        # Reusing a UI identity with different source contents cannot overwrite it.
        stable_fields = ('kind', 'actor_name', 'notification_text')
        if any(stored['incoming'][k] != item[k] for k in stable_fields):
            raise ValueError('interaction_source_changed')
        return stored

    def pending(self, session_id, account_id, limit=20):
        with self.connect() as db:
            rows = db.execute('SELECT record FROM interactions WHERE session_id=? AND account_id=? '
                              'ORDER BY rowid', (session_id, account_id)).fetchall()
            claims = {row[0] for row in db.execute('SELECT target_key FROM reply_claims')}
        values = [json.loads(row[0]) for row in rows]
        return [x for x in values if x['attention_state'] == 'pending'
                and (not x['target'] or digest([account_id, x['target']['identity']]) not in claims)
                ][:max(1, min(limit, 100))]

    def project(self, item):
        """Read existing reply facts from their authority rather than copy a status."""
        value = material(item)
        if item.get('target'):
            key = digest([item['account']['account_id'], item['target']['identity']])
            with self.connect() as db:
                row = db.execute('SELECT r.record FROM reply_claims c JOIN replies r ON r.id=c.reply_id '
                                 'WHERE c.target_key=? AND r.session_id=?', (key, item['session_id'])).fetchone()
            if row:
                value['existing_reply'] = reply_receipt(json.loads(row[0]))
        return value

    def bind_read(self, interaction_id, session_id, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM interactions WHERE id=? AND session_id=?',
                             (interaction_id, session_id)).fetchone()
            if not row:
                raise ValueError('interaction_not_found')
            item = json.loads(row[0])
            target = validate_target(target, {**item['incoming'], 'source_ref': item['source_ref']})
            if item['target'] and item['target'] != target:
                raise ValueError('frozen_comment_material_changed')
            item.update(target=target, read_at=item['read_at'] or now(), state='read')
            db.execute('UPDATE interactions SET record=? WHERE id=?',
                       (json.dumps(item, ensure_ascii=False), interaction_id))
        return item

    def reply(self, reply_id, session_id):
        with self.connect() as db:
            row = db.execute('SELECT record FROM replies WHERE id=? AND session_id=?',
                             (reply_id, session_id)).fetchone()
        return json.loads(row[0]) if row else None

    def draft_reply(self, interaction_id, session_id, text, source_key):
        text = reply_text(text)
        item = self.get(interaction_id, session_id)
        if not item or not item['read_at'] or not item['target']:
            raise ValueError('read_original_comment_first')
        if not source_key:
            raise ValueError('reply_source_required')
        identity = digest([session_id, interaction_id, source_key])
        value = {'id': identity, 'interaction_id': interaction_id, 'session_id': session_id,
                 'account': item['account'], 'target': item['target'], 'text': text,
                 'state': 'draft', 'created_at': now(), 'updated_at': now(),
                 'receipt': {}, 'error': ''}
        with self.connect() as db:
            # A new turn/channel cannot repeat an unknown send to the same comment.
            key = digest([item['account']['account_id'], item['target']['identity']])
            claimed = db.execute('SELECT reply_id FROM reply_claims WHERE target_key=?', (key,)).fetchone()
            if claimed:
                old = self.reply(claimed[0], session_id)
                if not old:
                    raise ValueError('comment_reply_already_claimed')
                if old['text'] != text:
                    raise ValueError('comment_reply_already_claimed')
                return old
            db.execute('INSERT OR IGNORE INTO replies VALUES (?,?,?,?,?)',
                       (identity, interaction_id, session_id, 'draft', json.dumps(value, ensure_ascii=False)))
        stored = self.reply(identity, session_id)
        if stored['text'] != text or stored['target'] != item['target']:
            raise ValueError('frozen_reply_changed')
        return stored

    def transition(self, reply_id, session_id, expected, state, **updates):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM replies WHERE id=? AND session_id=?',
                             (reply_id, session_id)).fetchone()
            if not row:
                raise ValueError('reply_not_found')
            item = json.loads(row[0])
            if item['state'] not in expected:
                raise ValueError('reply_state_conflict')
            if state == 'submitting':
                key = digest([item['account']['account_id'], item['target']['identity']])
                db.execute('INSERT OR IGNORE INTO reply_claims VALUES (?,?)', (key, reply_id))
                owner = db.execute('SELECT reply_id FROM reply_claims WHERE target_key=?', (key,)).fetchone()[0]
                if owner != reply_id:
                    raise ValueError('comment_reply_already_claimed')
            item.update(updates, state=state, updated_at=now())
            db.execute('UPDATE replies SET state=?,record=? WHERE id=?',
                       (state, json.dumps(item, ensure_ascii=False), reply_id))
        return item

    def unfinished_attentions(self, session_id, account_id):
        """Recover only stages with evidence of no unknown model dispatch.

        Unknown dispatches remain queryable in the ledger and are never paid for
        again by a background scan. Frozen returned decisions need no new model.
        """
        with self.connect() as db:
            rows = db.execute('SELECT record FROM attention WHERE session_id=? '
                              "AND state IN ('reading','model_returned','decided','executing') ORDER BY rowid",
                              (session_id,)).fetchall()
        values = [json.loads(row[0]) for row in rows]
        return [value for value in values if value.get('account', {}).get('account_id') == account_id]

    def begin_attention(self, batch_id, session_id, interaction_ids, *, materials=None, account=None):
        if not batch_id or not session_id or not interaction_ids:
            raise ValueError('attention_identity_required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM attention WHERE batch_id=?', (batch_id,)).fetchone()
            if row:
                old = json.loads(row[0])
                if old['session_id'] != session_id or old['interaction_ids'] != interaction_ids:
                    raise ValueError('attention_binding_changed')
                return old, False
            for identity in interaction_ids:
                item = self.get(identity, session_id)
                if not item or item['attention_state'] != 'pending':
                    raise ValueError('interaction_attention_already_claimed')
            value = {'batch_id': batch_id, 'session_id': session_id, 'state': 'reading',
                     'interaction_ids': interaction_ids, 'created_at': now(), 'steps': [],
                     'materials': deepcopy(materials or []), 'account': deepcopy(account or {}),
                     'revision': 0}
            db.execute('INSERT INTO attention VALUES (?,?,?,?)',
                       (batch_id, session_id, 'reading', json.dumps(value, ensure_ascii=False)))
            for identity in interaction_ids:
                item = self.get(identity, session_id)
                item['attention_state'] = 'claimed'
                db.execute('UPDATE interactions SET record=? WHERE id=?',
                           (json.dumps(item, ensure_ascii=False), identity))
        return value, True

    def save_attention(self, value, *, expected=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM attention WHERE batch_id=?', (value['batch_id'],)).fetchone()
            if not row:
                raise ValueError('attention_not_found')
            old = json.loads(row[0])
            if old['session_id'] != value['session_id']:
                raise ValueError('attention_not_found')
            if (old.get('revision', 0) != value.get('revision', 0)
                    or (expected is not None and old['state'] not in expected)):
                raise ValueError('attention_state_conflict')
            updated = deepcopy(value)
            updated['revision'] = old.get('revision', 0) + 1
            db.execute('UPDATE attention SET state=?,record=? WHERE batch_id=?',
                       (updated['state'], json.dumps(updated, ensure_ascii=False), value['batch_id']))
        value['revision'] = updated['revision']


def material(item):
    return {key: deepcopy(item[key]) for key in
            ('id', 'account', 'incoming', 'state', 'created_at', 'read_at', 'target')}


def reply_receipt(item):
    return {key: deepcopy(item[key]) for key in
            ('id', 'interaction_id', 'account', 'target', 'text', 'state', 'created_at',
             'updated_at', 'receipt', 'error')}
