"""Persisted job lifecycle belongs to its job, not automatically to AI."""


def resource_subject(row):
    from workshop.context import _status_cards
    cards = _status_cards(row)
    eligible = [card for card in cards if card.get('kind') in {
        'plan', 'modified', 'failed', 'ready', 'quality_queued', 'quality_report', 'quality_blocked',
    }]
    subjects = {'workshop_job:' + card['job_id'] for card in eligible}
    return next(iter(subjects)) if len(subjects) == 1 else ''
