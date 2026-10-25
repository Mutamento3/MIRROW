"""Frozen evidence for person actions, separate from worker telemetry."""
import math


def k_action(action, revision, **anchors):
    return {'memory_action': {'version': 1, 'actor_id': 'k', 'action': action,
                              'revision': revision, **anchors}}


def _positive(value):
    return type(value) is int and value > 0


def card_actors(card):
    """Called only after the immutable receipt/message identity was checked."""
    kind = card.get('kind')
    # The local UI contribution producer writes text and revision atomically
    # after the user's mutation succeeds; desktop_codex remains a third party.
    if kind == 'inspiration_added':
        return ('human',) if (card.get('source') == 'human'
                             and _positive(card.get('revision'))
                             and isinstance(card.get('text'), str)
                             and bool(card['text'].strip())) else ()
    # Both approval producers persist this only after the human budget mutation
    # succeeds. A worker plan or a generated mention of Human cannot qualify.
    if kind == 'resource_approval' and card.get('source') == 'human_settings':
        percent = card.get('additional_percent')
        tokens = card.get('addition')
        if ((type(percent) in (int, float) and math.isfinite(percent) and percent > 0)
                or _positive(tokens)):
            return ('human',)
        return ()
    action = card.get('memory_action')
    if action is not None:
        if not isinstance(action, dict) or action.get('version') != 1 or action.get('actor_id') != 'k':
            return ()
        if not _positive(action.get('revision')):
            return ()
        verb = action.get('action')
        eligible = ((kind == 'accepted' and verb == 'create' and _positive(action.get('message_seq')))
                    or (kind == 'modified' and verb == 'modify' and _positive(action.get('message_seq'))
                        and isinstance(card.get('text'), str) and bool(card['text'].strip()))
                    or (kind == 'confirmed' and verb == 'confirm' and _positive(action.get('plan_version')))
                    or (kind == 'accepted_work' and verb == 'accept' and bool(action.get('artifact_id')))
                    or (kind == 'artifact_shared' and verb == 'share' and card.get('source')=='k'
                        and _positive(action.get('message_seq')) and bool(action.get('artifact_id'))))
        return ('k',) if eligible else ()
    # These fixed historical producers ran only after their AI command succeeded.
    if kind == 'modified' and isinstance(card.get('text'), str) and card['text'].strip():
        if card.get('source') == 'technical_recovery_of_existing_k_message':
            return ('k',) if (_positive(card.get('message_seq')) and _positive(card.get('prior_revision'))
                             and card.get('revision') == card['prior_revision'] + 1) else ()
        return ('k',) if _positive(card.get('revision')) and not card.get('source') else ()
    if kind == 'confirmed' and _positive(card.get('plan_version')) and _positive(card.get('revision')):
        return ('k',)
    if kind == 'accepted_work' and isinstance(card.get('artifact_id'), str) and card['artifact_id'] and _positive(card.get('revision')):
        return ('k',)
    if kind == 'accepted' and isinstance(card.get('brief'), str) and card['brief'].strip():
        return ('k',)
    return ()
