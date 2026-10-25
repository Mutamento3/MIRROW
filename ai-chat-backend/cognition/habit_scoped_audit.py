"""Explicit claim scope and full-context insufficiency review for a bounded cycle."""
import copy
from . import habit_local_contract as local
from .habit_claim_support import validate, references
from memory_v2.day_settlement_checkpoint import _digest

VERSION = 'habit-scoped-review-v2'
CONTRACT = '''本次独立审核每个 case 的待核问题，完整材料均为带来源的数据。
kind=claim 时，statement 是唯一待核认识，book_context 是完整旧栏目，只用于辨认主体、指代与原有条件。核对 statement 在旧文中的范围是否完整，再核对 selected_evidence 是否支持这个范围。栏目中的其他独立认识不属于本项核验目标。
完整 messages 用于理解语境和发现相反证据；肯定结论的事实依据必须在 selected_evidence 中，漏选必要相邻消息时指出需要补什么。AI 的发言只证明 AI 说了什么；人类伙伴原话和归属明确的设备记录才分别证明她的自述和设备观测。
自然语言允许忠实的等义概括。判断保留实际主体、日期、条件、计划/已发生和观察跨度；已记录量能证明下限，不因未观测部分未知而否定已知事实。
targets 按 t 保存完整旧栏目，每个栏目全文仅一次；case.target 或 allowed_targets 引用该表。身份与版本由程序绑定。
kind=no_supported_claim 时，仅回答完整本请求材料中是否有可支持的完整旧认识。没有就 verdict=material_insufficient、issue=none；有就 verdict=supported_claim_found、issue=missing_evidence，并提供 counterexample={"t":目标编号,"claim":"仅含已获支持事实、保留条件的连续旧文片段","evidence":[{"n":完整消息编号}]}。并列分句可分别成立时只选有依据的部分，指代由目标主体和完整栏目消解。旧文被反例推翻，也不能证明它成立；确无其他可支持认识仍是 material_insufficient。该结论不认证或删除原书。
有 revision_of 时，这是同一失败认识的修正。结合原命题及旧缺口核对是否补证或保留条件地收窄；另换问题、删除关键条件来避开旧问题用 issue=scope。旧审核意见只是待检验的意见，不是事实来源。
每个 case 返回一次：{"contract":"habit-scoped-review-v2","checks":[{"id":"原样case.id","checked_statement":"原样statement","verdict":"按kind选择上述结果","issue":"none|missing_evidence|scope|contradiction|no_support","finding":"具体对应或缺口","basis":[{"n":消息编号,"quote":"消息中连续原文"}]}]}。
kind=claim 的 verdict 仍为 supported|insufficient|contradicted。supported 的 claim 必须有选中证据 basis，issue=none。其余拒绝结果必须指出具体 issue；basis 保留真实引文，没有依据可为空。只判断当前问题；counterexample 是候选，仍需下一次独立审核。'''


def payload(rows, space, choices):
    subjects = {r['subject_id'] for r in rows}
    if len(subjects) != 1:
        raise ValueError('cycle_subject_scope')
    subject = next(iter(subjects))
    targets = [{'t': i, 'subject_id': subject, 'name': r['name'],
                'section': r['target']['section'], 'text': r['section_text']}
               for i, r in enumerate(choices, 1)]
    ids = {_digest(r['target']): i for i, r in enumerate(choices, 1)}
    visible = []
    for original in rows:
        row = {k: copy.deepcopy(v) for k, v in original.items() if k not in {'target', 'book_context', 'allowed_targets'}}
        if original['kind'] == 'claim': row['target'] = ids[_digest(original['target'])]
        else: row['allowed_targets'] = list(range(1, len(targets)+1))
        if 'revision_of' in row:
            row['revision_of']['target'] = ids[_digest(row['revision_of']['target'])]
        visible.append(row)
    from .maintenance_sources import model_materials
    return {'messages': model_materials(space), 'targets': targets, 'review_contract': VERSION, 'cases': visible}


def cases(request, report, space, snapshot):
    choices = local.choices(request, snapshot)
    n_of = {e['id']: i for i, e in enumerate(space, 1)}
    if report['outcome'] == 'insufficient':
        rows = [{'kind': 'no_supported_claim', 'subject_id': request['subject_id'],
                 'statement': '本请求完整材料没有可支持的完整旧认识',
                 'allowed_targets': choices, 'selected_evidence': []}]
    elif report['outcome'] == 'supported_no_change':
        claims = validate(report.get('claim_support'), report['targets'], request, space, snapshot)
        if claims is None or references(claims) != set(report['reference_ids']):
            raise ValueError('cycle_claim_invalid')
        rows = []
        for claim in claims:
            target = report['targets'][claim['target_index'] - 1]
            item = next(c for c in choices if c['target'] == target)
            rows.append({'kind': 'claim', 'statement': claim['claim'], 'target': target,
                'subject_id': request['subject_id'], 'book_context': item['section_text'],
                'selected_evidence': [{'n': n_of[e['message_id']], 'quote': e['quote']}
                                      for e in claim['evidence']]})
    else:
        raise ValueError('cycle_requires_factual_proposal_workflow')
    # Whole request sources bind both contradiction context and the claim proof.
    for row in rows:
        row['id'] = _digest({'contract': VERSION, 'request': request, 'case': row, 'sources': space})
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('cycle_duplicate_claim')
    return rows


def assess(raw, rows, space):
    from wander_manager.flash_structured import parse_json_object
    value = parse_json_object(raw) if isinstance(raw, str) else raw
    checks = value.get('checks') if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get('contract') != VERSION or not isinstance(checks, list):
        raise ValueError('cycle_review_contract')
    by_id = {r['id']: r for r in rows}
    ids = [r.get('id') for r in checks if isinstance(r, dict)]
    if (len(ids) != len(checks) or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or set(ids) != set(by_id)):
        raise ValueError('cycle_review_coverage')
    result = {}
    for check in checks:
        row = by_id[check['id']]
        if check.get('checked_statement') != row['statement']:
            raise ValueError('cycle_review_wrong_scope')
        verdict, issue = check.get('verdict'), check.get('issue')
        if row['kind'] == 'no_supported_claim':
            if verdict == 'material_insufficient':
                verdict = 'supported'
            elif verdict == 'supported_claim_found':
                verdict = 'insufficient'
                counter = check.get('counterexample')
                if (not isinstance(counter, dict) or type(counter.get('t')) is not int
                        or not 1 <= counter['t'] <= len(row['allowed_targets'])
                        or not isinstance(counter.get('claim'), str) or not counter['claim'].strip()
                        or counter['claim'] not in row['allowed_targets'][counter['t']-1]['section_text']
                        or not isinstance(counter.get('evidence'), list) or not counter['evidence']
                        or any(not isinstance(e, dict) or set(e) != {'n'} or type(e['n']) is not int
                               or not 1 <= e['n'] <= len(space) for e in counter['evidence'])):
                    raise ValueError('cycle_review_counterexample_invalid')
            else:
                raise ValueError('cycle_review_insufficiency_answer')
        if (verdict not in {'supported', 'insufficient', 'contradicted'}
                or issue not in {'none', 'missing_evidence', 'scope', 'contradiction', 'no_support'}
                or (verdict == 'supported') != (issue == 'none')
                or not isinstance(check.get('finding'), str) or not check['finding'].strip()
                or not isinstance(check.get('basis'), list)):
            raise ValueError('cycle_review_verdict')
        if row['kind'] == 'claim' and verdict == 'supported' and not check['basis']:
            raise ValueError('cycle_review_missing_basis')
        for ref in check['basis']:
            if (not isinstance(ref, dict) or type(ref.get('n')) is not int
                    or not 1 <= ref['n'] <= len(space) or not isinstance(ref.get('quote'), str)
                    or not ref['quote'].strip() or ref['quote'] not in space[ref['n']-1]['text']):
                raise ValueError('cycle_review_bad_basis')
            if row['kind'] == 'claim' and verdict == 'supported' and not any(
                    e['n'] == ref['n'] and ref['quote'] in e['quote'] for e in row['selected_evidence']):
                raise ValueError('cycle_review_unselected_basis')
        result[row['id']] = {**copy.deepcopy(check), 'verdict': verdict, 'provider_verdict': check['verdict']}
    return result
