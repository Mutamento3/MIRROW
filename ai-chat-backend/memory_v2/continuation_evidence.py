"""Attach verified complete source units to continuation judgment requests."""
import copy
import json
from dataclasses import asdict

from .conversation_source import digest_text
from .models import SourceRef


def manifest(connection, events):
    result = []
    for event in events.values():
        sources = []
        for ref in event.sources:
            row = connection.execute(
                'SELECT bs.content_digest FROM events e JOIN encoding_batch_sources bs ON bs.batch_id=e.batch_id '
                'WHERE e.id=? AND bs.message_id=? AND bs.message_row_id=?',
                (event.event_id, ref.message_id, ref.message_row_id)).fetchone()
            if row is None:
                raise ValueError('continuation_source_manifest_missing')
            sources.append({**asdict(ref), 'content_digest': row[0]})
        result.append({'event_ref': event.ref, 'sources': sources})
    return tuple(result)


class SourceBoundContinuationModel:
    """No invented context or clipped text; absent/stale sources stop the request."""
    def __init__(self, source, model, capture_returned=None):
        self.source, self.model = source, model
        self.capture_returned = capture_returned

    def _material(self, entries):
        refs, hashes = [], {}
        for entry in entries:
            for row in entry['sources']:
                ref = SourceRef(**{k: v for k, v in row.items() if k != 'content_digest'})
                refs.append(ref)
                old = hashes.setdefault(ref.message_id, row['content_digest'])
                if old != row['content_digest']:
                    raise ValueError('continuation_source_versions_conflict')
        if not refs or not self.source.validate_refs(refs):
            raise ValueError('continuation_source_refs_changed')
        messages = self.source.read_message_ids(tuple(hashes))
        if len(messages) != len(hashes) or any(digest_text(m.content) != hashes[m.message_id] for m in messages):
            raise ValueError('continuation_source_body_changed')
        numbered = {m.message_id: 's' + str(i) for i, m in enumerate(messages)}
        return {
            'sources': [{'ref': numbered[m.message_id], 'time': m.timestamp, 'active_date': m.active_date,
                         'role': m.role, 'source_kind': m.source_kind, 'event_type': m.event_type,
                         'text': m.content, 'recorded_music': copy.deepcopy(list(m.recorded_music))}
                        for m in messages],
            'event_evidence': [{'event_ref': e['event_ref'], 'sources': [
                {'ref': numbered[r['message_id']], 'span_start': r['span_start'], 'span_end': r['span_end']}
                for r in e['sources']]} for e in entries],
        }

    def prepare(self, kwargs):
        messages = copy.deepcopy(kwargs['messages'])
        payload = json.loads(messages[-1]['content'])
        entries = payload.pop('source_manifest', None)
        if entries is None:
            return kwargs
        payload.update(self._material(entries))
        messages[-1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        return {**kwargs, 'messages': messages}

    async def __call__(self, **kwargs):
        messages = copy.deepcopy(kwargs['messages'])
        payload = json.loads(messages[-1]['content'])
        entries = payload.pop('source_manifest', None)
        if entries is None:  # Original wire for an explicit legacy retry.
            return await self.model(**kwargs)
        evidence = self._material(entries)
        payload.update(evidence)
        messages[-1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        result = await self.model(**{**kwargs, 'messages': messages})
        if self.capture_returned is not None:
            self.capture_returned(result)
        if self._material(entries) != evidence:
            raise ValueError('continuation_sources_changed_during_request')
        return result

    def safe_usage(self):
        return self.model.safe_usage() if hasattr(self.model, 'safe_usage') else {}
