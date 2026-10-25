"""Freeze the new habit cycle only for new natural daily receipts."""
import copy
import json
from . import books
from .habit_review_cycle import MODE

START_DATE = '2026-10-24'
POLICY = {'version': 'natural-habit-cycle-v1', 'mode': MODE,
          'calls_per_request': 4, 'max_calls_per_run': 96,
          'max_provider_bytes': 2000000, 'usage_estimate_limit_cny': '8.00'}


def validate(policy):
    if policy != POLICY:
        raise books.BookError('自然习惯执行合同不匹配，保留原任务')
    return policy


def for_daily(path, source_date, session_id):
    if path.exists():
        # Old receipts lacking the field remain legacy, including failed lanes.
        policy = json.loads(path.read_text('utf-8')).get('habit_cycle_policy')
        return copy.deepcopy(validate(policy)) if policy is not None else None
    if source_date < START_DATE:
        return None
    for prior_path in path.parent.glob('*.json'):
        prior = json.loads(prior_path.read_text('utf-8'))
        if (prior.get('source_date'), prior.get('session_id')) == (source_date, session_id):
            if prior.get('habit_cycle_policy') is None:
                return None
            validate(prior['habit_cycle_policy'])
    # A lost/new daily envelope is not permission to migrate an existing world
    # run for the same active day. Keep its original lane contract.
    for prior_path in (path.parent.parent / 'runs').glob('*.json'):
        prior = json.loads(prior_path.read_text('utf-8'))
        if (prior.get('source_date'), prior.get('session_id')) == (source_date, session_id):
            frozen = prior.get('extraction_material', {})
            if prior.get('evolution_version') == 2 and frozen.get('habit_cycle_policy') != POLICY:
                return None
    return copy.deepcopy(POLICY)


def limits(policy, request_count):
    validate(policy)
    return {'max_calls': min(policy['calls_per_request'] * request_count, policy['max_calls_per_run']),
            'max_provider_bytes': policy['max_provider_bytes'],
            'max_cost_cny': policy['usage_estimate_limit_cny']}


def status():
    return {'new_daily_from': START_DATE, **copy.deepcopy(POLICY),
            'existing_daily_without_policy': 'legacy',
            'limits_defer_whole_request': True}
