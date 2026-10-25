"""One-shot source-backed summary evaluation, never a production repair.

The caller selects complete messages. The new contract can be compared on old
material without changing dates, source projections or production receipts.
Only a private report is saved; no memory, cognition or scene store is opened.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .conversation_source import ConversationSource
from .encoding import build_encoding_messages, build_encoding_json_schema, EncodingContractError, assess_encoding_quality
from .experience_summary_contract import (VERSION, CANDIDATE_VERSION, ATTRIBUTION_CANDIDATE_VERSION,
                                          SECTIONED_CANDIDATE_VERSION, ADMISSION_CANDIDATE_VERSION,
                                          evaluation_guidance, SOURCE_BOUND_VERSION)
from .experiment import _call_flash_once, _parse_encoding_with_safe_narrowing, _safe_usage
from .replay import ReplayPlanner, ReplayConfig
from . import summary_parts_candidate as parts_candidate
from . import retained_account_candidate as retained_candidate
from .encoding_admission import VERSIONS as ADMISSION_VERSIONS


DEFAULT_PROBE_MAX_TOKENS = 4096
PART_VERSIONS = parts_candidate.VERSIONS | set(retained_candidate.VERSIONS)


def probe_schema(version, rows):
    if version in retained_candidate.VERSIONS:
        return retained_candidate.build_schema(rows, version=version)
    return (parts_candidate.build_schema(rows, version=version) if version in parts_candidate.VERSIONS else
            build_encoding_json_schema(experience_version=version))


def parse_probe_output(raw, plan, rows, version, *, record_excerpts=False, include_account_narrative=False):
    if record_excerpts and version not in parts_candidate.VERSIONS:
        raise ValueError('probe_record_excerpts_requires_parts_contract')
    if include_account_narrative and version not in retained_candidate.VERSIONS:
        raise ValueError('probe_account_narrative_requires_retained_contract')
    value = (retained_candidate.canonical_output(raw, rows, include_account_narrative=include_account_narrative)
             if version in retained_candidate.VERSIONS else
             parts_candidate.canonical_output(raw, rows, record_excerpts=record_excerpts)
             if version in parts_candidate.VERSIONS else raw)
    return _parse_encoding_with_safe_narrowing(value, plan, rows)


def prepare_probe(source, message_ids, *, experience_version=VERSION, enable_thinking=False,
                  source_namespace='summary-evaluation'):
    if source_namespace not in {'mainline', 'summary-evaluation'}:
        raise ValueError('probe_invalid_source_namespace')
    base_version = SECTIONED_CANDIDATE_VERSION if experience_version in PART_VERSIONS else experience_version
    evaluation_guidance(base_version)
    rows = source.read_message_ids(message_ids)
    if len(rows) != len(message_ids) or not rows:
        raise ValueError('probe_selection_not_fully_available')
    rows.sort(key=lambda row: (row.timestamp, row.row_id))
    if len({(row.session_id, row.active_date) for row in rows}) != 1:
        raise ValueError('probe_requires_one_session_and_active_day')
    planner = ReplayPlanner(source, ReplayConfig(source_namespace=source_namespace))
    plans = tuple(planner._plan_session(rows))
    if len(plans) != 1 or plans[0].oversized_single_message:
        raise ValueError('probe_exceeds_one_complete_batch')
    plan = plans[0]
    messages = build_encoding_messages(plan, rows, experience_version=base_version)
    if experience_version in retained_candidate.VERSIONS:
        messages = retained_candidate.build_messages(messages, rows, version=experience_version)
    elif experience_version in parts_candidate.VERSIONS:
        messages = parts_candidate.build_messages(messages, rows, version=experience_version)
    if enable_thinking:
        # Thinking transport uses JSON object mode rather than native schema.
        # Supply the same output contract explicitly, with a digest of what is sent.
        messages[0]['content'] += '\nOutput JSON schema:\n' + json.dumps(
            probe_schema(experience_version, rows),
            ensure_ascii=False, separators=(',', ':'),
        )
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
    return plan, rows, messages, hashlib.sha256(payload).hexdigest()


async def probe_summary(source, message_ids, *, provider=None, experience_version=VERSION,
                        enable_thinking=False, max_tokens=DEFAULT_PROBE_MAX_TOKENS,
                        source_namespace='summary-evaluation'):
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError('probe_invalid_max_tokens')
    plan, rows, messages, request_digest = prepare_probe(source, message_ids,
                                                        experience_version=experience_version,
                                                        enable_thinking=enable_thinking,
                                                        source_namespace=source_namespace)
    schema = probe_schema(experience_version, rows)
    report = {'contract': experience_version, 'request_digest': request_digest,
              'source_namespace': source_namespace,
              'thinking_requested': bool(enable_thinking),
              'native_schema_requested': not bool(enable_thinking),
              'schema_in_prompt': bool(enable_thinking),
              'schema_digest': hashlib.sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              'active_date': plan.batch.active_date, 'source_count': len(rows),
              'source_refs': [asdict(ref) for ref in plan.batch_sources],
              'estimated_source_tokens': plan.estimated_tokens, 'request_count': 1,
              'requested_max_tokens': max_tokens, 'production_writes': False, 'events': []}
    if experience_version in {SECTIONED_CANDIDATE_VERSION, SOURCE_BOUND_VERSION} or experience_version in PART_VERSIONS:
        report['source_parts_digest'] = parts_candidate.source_parts_digest(rows)
    result = await (provider or _call_flash_once)(
        messages, max_tokens=max_tokens, usage_tag='memory_v2_summary_probe', schema=schema,
        enable_thinking=enable_thinking)
    report.update(status=result.status, model=result.model, duration_ms=result.duration_ms,
                  usage=_safe_usage(result.usage) or {}, finish_reason=result.finish_reason)
    if result.status != 'ok':
        # Never retain transport exceptions/endpoints. A one-shot probe does not retry.
        return report
    # Preserve paid visible output for offline review, not hidden reasoning.
    report['unvalidated_output'] = result.raw_content
    if not source.validate_batch_sources(plan.batch_sources):
        return {**report, 'status': 'source_changed'}
    if 'source_parts_digest' in report:
        current = source.read_message_ids(tuple(ref.message_id for ref in plan.batch_sources))
        current.sort(key=lambda row: (row.timestamp, row.row_id))
        if parts_candidate.source_parts_digest(current) != report['source_parts_digest']:
            return {**report, 'status': 'source_changed'}
    try:
        parsed = parse_probe_output(result.raw_content, plan, rows, experience_version)
    except EncodingContractError as exc:
        from .online_recovery import safe_contract_check
        check = str(exc) if str(exc) in parts_candidate.ERROR_CODES | retained_candidate.ERROR_CODES else safe_contract_check(exc)
        return {**report, 'status': 'contract_error', 'contract_check': check}
    if parsed.overflow or parsed.overflow_source_refs:
        return {**report, 'status': 'overflow_unresolved'}
    report.update(status='validated', quality=assess_encoding_quality(parsed, plan).safe_dict(),
                  non_event_source_refs=list(parsed.non_event_source_refs),
                  events=[{'subject_id': event.subject_id, 'event_type': event.event_type,
                           'summary': event.summary, 'occurred_at': event.occurred_at,
                           'reported_at': event.reported_at,
                           'source_message_ids': [ref.message_id for ref in event.sources],
                           'attributes': dict(event.attributes)} for event in parsed.events])
    if experience_version in PART_VERSIONS:
        report['summary_render_version'] = (retained_candidate.RENDER_VERSION
            if experience_version in retained_candidate.VERSIONS else parts_candidate.RENDER_VERSION)
    return report


def verify_probe_reading(report, source, query, *, record_excerpts=False, include_account_narrative=False):
    """Replay saved output through isolated storage and the real read projection."""
    import tempfile
    from datetime import date
    from .store import MemoryV2Store
    from .experiment import _commit_encoding
    from .recall import EventRecallIndex, RecallQuery
    from .recall_service import run_shadow_recall
    from .recall_context import build_recall_context

    if report.get('status') != 'validated':
        raise ValueError('probe_report_not_validated')
    ids = tuple(ref['message_id'] for ref in report['source_refs'])
    plan, rows, _, request_digest = prepare_probe(source, ids, experience_version=report['contract'],
                                    enable_thinking=bool(report.get('thinking_requested')),
                                    source_namespace=report.get('source_namespace', 'summary-evaluation'))
    if [asdict(ref) for ref in plan.batch_sources] != report['source_refs']:
        raise ValueError('probe_report_source_changed')
    if (report.get('source_parts_digest') is not None
            and report['source_parts_digest'] != parts_candidate.source_parts_digest(rows)):
        raise ValueError('probe_report_source_changed')
    schema_digest = hashlib.sha256(json.dumps(probe_schema(report['contract'], rows),
                                              ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if request_digest != report.get('request_digest') or schema_digest != report.get('schema_digest'):
        raise ValueError('probe_report_contract_changed')
    parsed = parse_probe_output(report['unvalidated_output'], plan, rows, report['contract'],
                                record_excerpts=record_excerpts, include_account_narrative=include_account_narrative)
    with tempfile.TemporaryDirectory() as root:
        store = MemoryV2Store(Path(root) / 'probe.db')
        event_ids = _commit_encoding(store, source, plan, parsed)
        execution = run_shadow_recall(EventRecallIndex(store.db_path), source, RecallQuery(query),
                                     reference_date=date.fromisoformat(report['active_date']),
                                     force_source_detail=True)
        view = build_recall_context(execution.result, execution.evidence)
    renderer = ({'summary_render_version': (retained_candidate.NARRATIVE_RENDER_VERSION if include_account_narrative else
                                          parts_candidate.EXCERPT_RENDER_VERSION if record_excerpts else
                                          retained_candidate.RENDER_VERSION if report['contract'] in retained_candidate.VERSIONS
                                          else parts_candidate.RENDER_VERSION),
                 'generation_render_version': report.get('summary_render_version', 'unversioned_probe')}
                if report['contract'] in PART_VERSIONS else {})
    return {'status': 'reading_replayed', 'contract': report['contract'], **renderer,
            'active_date': plan.batch.active_date, 'source_count': len(rows),
            'production_writes': False, 'request_count': 0, 'event_count': len(event_ids),
            'reading': execution.safe_observation(), 'projection': view.safe_observation(),
            'rendered_event_ids': list(view.rendered_event_ids),
            'anchor_message_ids': [row.message_id for group in execution.evidence.groups
                                   for row in group.messages if row.is_anchor],
            'k_facing_text': view.text}


async def _main(args):
    # Check before spending tokens, and refuse overwriting any earlier paid output.
    if args.output.exists():
        raise ValueError('probe_report_already_exists')
    if args.record_excerpts and not args.verify_report:
        raise ValueError('probe_record_excerpts_requires_saved_report')
    if args.include_account_narrative and not args.verify_report:
        raise ValueError('probe_account_narrative_requires_saved_report')
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    source = ConversationSource(args.db, canonical_sessions_only=True)
    if args.verify_report:
        if not args.query:
            raise ValueError('probe_reading_requires_query')
        with args.verify_report.open(encoding='utf-8') as handle:
            report = verify_probe_reading(json.load(handle), source, args.query,
                                          record_excerpts=args.record_excerpts,
                                          include_account_narrative=args.include_account_narrative)
    else:
        report = await probe_summary(source, args.message_ids, experience_version=args.experience_version,
                                     enable_thinking=args.enable_thinking, max_tokens=args.max_tokens,
                                     source_namespace=args.source_namespace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: report.get(key) for key in (
        'status', 'contract', 'active_date', 'source_count', 'request_count', 'model', 'usage')},
        ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--message-ids', nargs='+')
    selection.add_argument('--verify-report', type=Path)
    parser.add_argument('--query')
    parser.add_argument('--record-excerpts', action='store_true',
                        help='Zero-call read comparison: exact record excerpts, attributed account summaries.')
    parser.add_argument('--include-account-narrative', action='store_true',
                        help='Zero-call retained-account comparison: attributed narrative plus exact personal quote.')
    parser.add_argument('--enable-thinking', action='store_true',
                        help='One bounded thinking comparison; hidden reasoning is never retained.')
    parser.add_argument('--max-tokens', type=int, default=DEFAULT_PROBE_MAX_TOKENS,
                        help='Explicit output budget for this isolated generation, without changing production.')
    parser.add_argument('--experience-version', choices=(VERSION, CANDIDATE_VERSION, ATTRIBUTION_CANDIDATE_VERSION,
                                                       SECTIONED_CANDIDATE_VERSION, SOURCE_BOUND_VERSION, *ADMISSION_VERSIONS,
                                                       parts_candidate.VERSION,
                                                       parts_candidate.NARRATIVE_VERSION,
                                                       parts_candidate.COMPACT_VERSION,
                                                       *retained_candidate.VERSIONS), default=VERSION,
                        help='Explicit offline candidate; does not change production date gates.')
    parser.add_argument('--source-namespace', choices=('mainline', 'summary-evaluation'),
                        default='summary-evaluation', help='Parser admission namespace; storage remains isolated.')
    parser.add_argument('--output', type=Path, required=True,
                        help='Private local output; never put a real-source report in Git.')
    asyncio.run(_main(parser.parse_args()))
