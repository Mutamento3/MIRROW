"""Private, source-bound checkpoints for explicit reference-only recovery.

These files are not book authority or diagnostics. Reads never call a model;
an explicit review returns candidates, never commits or clears daily issues.
"""
import copy
import json

from . import books, maintenance

VERSION = 'reference-candidates-v1'


def path(run_id):
    # Reuse the ledger's ID validation, without placing candidate prose in its
    # externally consumable item or daily diagnostics records.
    maintenance.record_path(run_id)
    return maintenance.folder() / 'reference_candidates' / (run_id + '.json')


def binding(space, source_payload):
    return maintenance.digest({'space': space, 'source_payload': source_payload})


def candidate_binding(output):
    return maintenance.digest({key: [{k: v for k, v in item.items() if k != 'reference_ids'}
                                    if isinstance(item, dict) else item
                                    for item in output.get(key, [])]
                               for key in ('entities', 'entries')})


def checkpoint_for(space, source_payload, *, source_date, run_id, resuming=False):
    frozen_space, frozen_payload = copy.deepcopy(space), copy.deepcopy(source_payload)
    source_binding = binding(frozen_space, frozen_payload)
    started = False

    def checkpoint(output, audit):
        nonlocal started
        target = path(run_id)
        saved = json.loads(target.read_text('utf-8')) if target.exists() else None
        if saved is not None and (saved.get('version'), saved.get('run_id'),
                saved.get('source_date')) != (VERSION, run_id, source_date):
            raise books.BookError('隔离候选来源身份不匹配，保留原件')
        changed = saved is not None and (saved.get('source_binding') != source_binding or
                candidate_binding(saved['original_output']) != candidate_binding(output))
        if changed and resuming:
            raise books.BookError('隔离候选引用空间或正文已变化，保留原件')
        if saved is not None and (changed or not started and not resuming):
            # A fresh extraction after an all-rejected attempt is legitimate,
            # including changed recall background. Keep both source spaces;
            # new indexes must never be attached to the previous prose.
            archive = target.parent / run_id / (maintenance.digest(saved) + '.json')
            books.atomic_text(archive, json.dumps(saved, ensure_ascii=False, indent=2))
            saved = None
        if saved is None:
            saved = {'version': VERSION, 'run_id': run_id, 'source_date': source_date,
                     'source_binding': source_binding, 'space': frozen_space,
                     'source_payload': frozen_payload,
                     'original_output': copy.deepcopy(output),
                     'original_rejections': copy.deepcopy(audit['initial_rejections']),
                     'recovery_targets': copy.deepcopy(audit['initial_rejections'])}
        if not resuming and 'remaining_rejections' in audit:
            # Candidates repaired by the original call proceed into the normal
            # book commit already. Explicit recovery must not return them again.
            saved['recovery_targets'] = copy.deepcopy(audit['remaining_rejections'])
        saved.update(output=copy.deepcopy(output), audit=copy.deepcopy(audit),
                     status='pending' if 'remaining_rejections' not in audit else
                     'isolated' if audit['remaining_rejections'] else 'reviewed')
        books.atomic_text(target, json.dumps(saved, ensure_ascii=False, indent=2))
        started = True

    return checkpoint


def load(run_id, *, source_date):
    target = path(run_id)
    if not target.exists():
        raise books.BookError('旧隔离候选正文未保留，需要按原材料另做维护')
    saved = json.loads(target.read_text('utf-8'))
    if (saved.get('version'), saved.get('run_id'), saved.get('source_date')) != (
            VERSION, run_id, source_date):
        raise books.BookError('隔离候选来源身份不匹配，保留原件')
    if binding(saved['space'], saved['source_payload']) != saved['source_binding']:
        raise books.BookError('隔离候选引用空间已变化，保留原件')
    return saved


async def review_saved(run_id, llm, *, source_date):
    """Explicit one-call reference review using frozen original materials.

Returns only initially isolated candidates and the content-free audit. Book
revision and semantic freshness still need normal maintenance before commit.
Accepted book prose and original daily receipts are never rewritten here.
"""
    from .reference_review import review
    saved = load(run_id, source_date=source_date)
    output = copy.deepcopy(saved['output'])
    _, audit = await review(output, saved['space'], llm,
        source_date=source_date, run_id=run_id, source_payload=saved['source_payload'],
        checkpoint=checkpoint_for(saved['space'], saved['source_payload'],
                                  source_date=source_date, run_id=run_id, resuming=True))
    # The unfiltered checkpoint preserves original indexes. The filtered return
    # from review must not be indexed with coordinates from the old full output.
    latest = load(run_id, source_date=source_date)
    rejected = {(d['kind'], d['index']) for d in audit['remaining_rejections']}
    recovered = {'entities': [], 'entries': []}
    for diagnostic in latest['recovery_targets']:
        kind, index = diagnostic['kind'], diagnostic['index']
        if (kind, index) not in rejected:
            key = 'entities' if kind == 'entity' else 'entries'
            recovered[key].append(copy.deepcopy(latest['output'][key][index]))
    return {'status': 'isolated' if rejected else 'reviewed',
            'output': recovered, 'reference_review': audit,
            'source_binding': latest['source_binding']}
