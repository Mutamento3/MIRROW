"""Small, source-bound semantic gate; no book writes or automatic retries."""
import json
from pathlib import Path
from . import maintenance
from .habit_claim_support import model_report
from .prompt_request import CognitionPrompt
from memory_v2.day_settlement_checkpoint import atomic_write, _digest

VERSION = 'habit-claim-entailment-v1'
ROUTE = 'cognition_habit_claim_review'
MAX_REQUEST_BYTES = 125000
MAX_OPERATOR_REQUEST_BYTES = 250000
MAX_REPORTS = 8
MAX_CLAIMS = 32
CONTRACT = '''逐项审核这些习惯复核报告，只依据各请求 reference_ids 对应的完整来源与引文。
旧认识是待验证的命题，不是证明自己的证据。每条 claim 分别核验主体、时间/计划或已发生、具体内容。
较早的计划只证明当时计划，不能证明后来已经执行；同领域多次讨论不能证明具体群名、成员或事件。
输出 JSON {"reviews":[{"id":"请求id","verdict":"supported|unsupported|insufficient",
"claims":[{"index":1,"subject":"supported|unsupported|unknown","time":"supported|unsupported|unknown",
"content":"supported|unsupported|unknown"}]}]}，每个请求恰好一项，每条 claim 恰好一项。
supported_no_change 仅所有维度确有支持时 verdict=supported；不符用 unsupported，信息不足用 insufficient。
报告本身为 insufficient 时，审核其“尚无材料支持目标认识”的判断；确实不足用 verdict=insufficient、claims=[]。
批准材料不足报告不等于确认原认识正确或错误。无需生成新认识、改书或解释推理。
'''


def dimensions(report):
    from .habit_local_contract import SPAN_VERSION
    return ('subject', 'time', 'content') + (
        ('scope',) if report.get('selection_contract') == SPAN_VERSION else ())


def contract(reports):
    if not any('scope' in dimensions(r) for r in reports):
        return CONTRACT
    return CONTRACT + (
        '\n原文片段报告的每条 claim 另返回 scope=supported|unsupported|unknown，单独核验适用范围。'
        '将片段作为脱离旧栏目也能成立的陈述，与 targets 完整原文对照：主体、否定、条件、频率或时间'
        '有遗漏而扩大适用范围时 scope=unsupported；不能确定则 scope=unknown。旧栏目仅供比较命题范围。'
        '证据须支持片段的全部事实；一次例子不能证明常用或习惯。完整来源用于理解上下文，实际支持'
        '必须来自该 claim 已选 evidence；其他背景不能代补未选证据。仅四个维度全部 supported 才批准。'
        '只批准实际核验的片段，未列部分仍未复核。')


def eligible_report(request, report):
    return (request.get('entailment_contract') == VERSION and
            (report['outcome'] == 'supported_no_change' or
             report['outcome'] == 'insufficient' and bool(request.get('baseline_targets'))))


def material(requests, reports, evidence):
    chosen = {r['id']: r for r in requests if r.get('entailment_contract') == VERSION}
    reports = [r for r in reports if r['id'] in chosen and eligible_report(chosen[r['id']], r)]
    ids = {mid for r in reports for mid in chosen[r['id']]['reference_ids']}
    space = [e for e in evidence if e['id'] in ids]
    n_of = {e['id']: i for i,e in enumerate(space,1)}
    from . import books
    targets = [t for r in reports for t in (chosen[r['id']].get('baseline_targets', [])
               if r['outcome'] == 'insufficient' else r.get('targets', []))]
    entries = [e for domain in books.DOMAINS for e in books.catalog(domain)
               if any(t['domain']==domain and t['entry_id']==e['entry_id'] and t['revision']==e['revision'] for t in targets)]
    return reports, {'messages':[{**{k:e[k] for k in ('speaker','text','timestamp','active_date','source_kind') if k in e},
                                'n':n_of[e['id']]} for e in space],
                     'targets':[{k:e[k] for k in ('domain','entry_id','revision','subject_id','body')} for e in entries],
                     'requests':[{**{k:chosen[r['id']][k] for k in ('id','subject_id','dimension','baseline_targets')},
                                  'reference_ids':[n_of[m] for m in chosen[r['id']]['reference_ids']]} for r in reports],
                     'reports':[model_report(r,n_of) for r in reports]}


def assess(raw, reports):
    from wander_manager.flash_structured import parse_json_object
    value = parse_json_object(raw) if isinstance(raw,str) else raw
    rows = value.get('reviews') if isinstance(value,dict) else None
    if not isinstance(rows,list) or len(rows)!=len(reports): raise ValueError('claim_review_coverage')
    by_id = {r.get('id'):r for r in rows if isinstance(r,dict)}
    if len(by_id)!=len(rows) or set(by_id)!={r['id'] for r in reports}: raise ValueError('claim_review_identity')
    result=[]; decisions=[]
    for report in reports:
        row=by_id[report['id']];claims=row.get('claims')
        checks=dimensions(report)
        if not isinstance(claims,list) or row.get('verdict') not in {'supported','unsupported','insufficient'}:
            raise ValueError('claim_review_contract')
        expected=list(range(1,len(report.get('claim_support',[]))+1)) if report['outcome']=='supported_no_change' else []
        if any(not isinstance(c,dict) or type(c.get('index')) is not int for c in claims):
            raise ValueError('claim_review_claims')
        if sorted(c['index'] for c in claims)!=expected: raise ValueError('claim_review_claims')
        if any(c.get(k) not in {'supported','unsupported','unknown'} for c in claims for k in checks):
            raise ValueError('claim_review_verdict')
        if (report['outcome']=='insufficient' and row['verdict']=='insufficient' or
            report['outcome']=='supported_no_change' and row['verdict']=='supported' and
            all(c[k]=='supported' for c in claims for k in checks)): result.append(report['id'])
        decisions.append({'id': report['id'], 'approved': report['id'] in result,
                          'verdict': row['verdict'],
                          'reason_code': ('claim_dimension_not_supported' if row['verdict'] == 'supported'
                                          and report['id'] not in result else row['verdict']), 'claims': [
                              {k: c[k] for k in ('index', *checks)} for c in claims]})
    return {'approved_ids': result, 'decisions': decisions}


def approved(raw, reports):
    return assess(raw, reports)['approved_ids']


async def provider(messages, on_start):
    from api_sources.single_source import resolve_single_source
    from api_sources.runner import run_structured_once
    selected=resolve_single_source(ROUTE)
    from memory_v2.continuation_review_transport import source_identity
    on_start(selected['model']['id'],_digest(source_identity(selected)))
    from llm_client import LLM_LONG_TIMEOUT
    return await run_structured_once(messages,sources=[selected],route=ROUTE,
        task_id=VERSION+'-'+_digest(messages)[:20],max_tokens=4096,temperature=1.0,
        request_timeout=LLM_LONG_TIMEOUT,timeout_seconds=int(LLM_LONG_TIMEOUT.read or 600),
        enable_thinking=None,json_mode=True,response_format=None,stop_on_output_error=True,inspect_provider=False)


def source_pin():
    from api_sources.single_source import resolve_single_source
    from memory_v2.continuation_review_transport import source_identity
    return _digest(source_identity(resolve_single_source(ROUTE,require_ready=False)))


def check_current_private_sources(evidence):
    from . import books
    if books.ROOT != Path(__file__).resolve().parents[1]:return  # Isolated fixtures own their frozen sources.
    selected=[e for e in evidence if e.get('source_kind')=='private_chat']
    from memory_v2.conversation_source import ConversationSource
    path=books.ROOT/'events/event_chronicle.db'
    source=ConversationSource(path,canonical_sessions_only=True)
    current={r.message_id:r for r in source.read_message_ids(tuple(e['id'] for e in selected))}
    # ConversationSource includes row-bound tool receipts for memory. Private
    # cognition statements were frozen from raw chat. Baseline-source collection
    # explicitly uses validated_canonical_chat and freezes the memory projection.
    # Preserve that existing binding rather than accepting either text loosely.
    import sqlite3
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        for e in selected:
            r=current.get(e['id'])
            raw=db.execute('SELECT content FROM conversation_messages WHERE id=?',
                           (r.row_id,)).fetchone() if r is not None else None
            content=(r.content if r is not None and e.get('identity_basis')=='validated_canonical_chat'
                     else str(raw[0] or '') if raw is not None else None)
            if (r is None or content is None or content.strip()!=e['text'].strip() or
                    ('human' if r.role=='user' else 'k' if r.role=='assistant' else '')!=e['speaker'] or
                    r.timestamp!=e.get('timestamp')):
                raise ValueError('claim_review_private_source_changed')


def saved_component(folder, key, report_id, pin, decisions=None, *, citation_audit=False):
    # An explicit operator correction denies reuse, never opens a new call.
    # Keep the provider result and original component pointer intact.
    if (folder/('hold-'+key+'.json')).exists():return False
    pointer=folder/('component-'+key+'.json')
    if not pointer.exists():return None
    record=json.loads(pointer.read_text('utf-8'));batch=record.get('batch','')
    if len(batch)!=64 or any(c not in '0123456789abcdef' for c in batch):return False
    state=json.loads((folder/(batch+'.json')).read_text('utf-8'))
    if (key not in state.get('component_keys',[]) or state.get('source_pin')!=pin or 'raw' not in state
            or state.get('raw_digest')!=_digest(state['raw'])):return False
    original=state['reports'][state['component_keys'].index(key)]['id']
    assessment = _assessment(state, citation_audit)
    if decisions is not None:
        decisions.extend({**d, 'id': report_id} for d in assessment['decisions'] if d['id'] == original)
    return original in assessment['approved_ids']


def _wire(requests, reports, evidence, citation_audit=False):
    if citation_audit:
        from . import habit_citation_review as citation, habit_evidence_audit as audit
        payload = citation.material(requests, reports, evidence)
        return reports, payload, CognitionPrompt(audit.CONTRACT, payload, stage='habit_claim_review').wire()[1]
    selected, payload = material(requests, reports, evidence)
    return selected, payload, CognitionPrompt(contract(selected), payload, stage='habit_claim_review').wire()[1]


def _assessment(state, citation_audit=False):
    if state.get('status') != 'ok':
        raise ValueError('claim_review_output_requires_attention')
    if citation_audit:
        from . import habit_citation_review as citation
        payload = state.get('citation_payload')
        if not payload or _digest(payload) != state.get('citation_payload_digest'):
            raise ValueError('citation_review_payload_changed')
        return citation.assess(state['raw'], state['reports'], payload)
    return assess(state['raw'], state['reports'])


def component_key(requests, report, evidence, *, citation_audit=False):
    if citation_audit:
        from .habit_evidence_audit import VERSION as version
        _, _, wire = _wire(requests, [report], evidence, True)
        return _digest({'version': version, 'component': wire})
    _,component=material(requests,[report],evidence)
    component['requests'][0]['id']='component'
    component['reports'][0]['id']='component'
    _,wire=CognitionPrompt(contract([report]),component,stage='habit_claim_review').wire()
    return _digest({'version':VERSION,'component':wire})


def operator_held(request, report, evidence):
    if not eligible_report(request,report):return False
    key=component_key([request],report,evidence)
    folder=maintenance.folder()/'habit_claim_outputs'
    if (folder/('hold-'+key+'.json')).exists():return True
    if report['outcome']=='supported_no_change':
        key=component_key([request],report,evidence,citation_audit=True)
        return (folder/('hold-'+key+'.json')).exists()
    return False


async def review(requests,reports,evidence,previous_approved,*,max_request_bytes=None,citation_audit=False):
    # Explicit operator override only; normal maintenance keeps its old limit.
    # This size admission does not replace the caller's monetary budget guard.
    limit=MAX_REQUEST_BYTES if max_request_bytes is None else max_request_bytes
    if type(limit) is not int or not 1<=limit<=MAX_OPERATOR_REQUEST_BYTES:
        raise ValueError('claim_review_size_limit_invalid')
    if type(citation_audit) is not bool:
        raise ValueError('citation_review_mode_invalid')
    from .habit_evidence_audit import VERSION as citation_version
    version = citation_version if citation_audit else VERSION
    marked={r['id']:r for r in requests if r.get('entailment_contract')==VERSION}
    eligible=[r for r in reports if r['id'] in marked and eligible_report(marked[r['id']],r)]
    affected={r['id'] for r in eligible}
    if not affected:return list(previous_approved), {'status':'not_required','model_calls':0}
    retained=[rid for rid in previous_approved if rid not in affected]
    folder=maintenance.folder()/'habit_claim_outputs';folder.mkdir(parents=True,exist_ok=True)
    selected=[];messages=[];component_keys=[];cached_count=0;deferred_components=[]; decisions=[]
    try:
        pin=source_pin()
        check_current_private_sources(evidence)
        from .habit_baseline_sources import check_current as check_baseline_sources
        check_baseline_sources([marked[r['id']] for r in eligible])
        from .habit_claim_support import validate, references
        known={r['id']:r for r in requests}
        for report in eligible:
            if citation_audit and report['outcome'] != 'supported_no_change':
                deferred_components.append({'id':report['id'],'reason':'requires_full_context_review'})
                continue
            if report['outcome']=='insufficient':
                from .habit_review import targets
                request=known[report['id']]
                bound=request['baseline_targets']
                if targets(bound,request['subject_id'])!=bound:continue
                claims=[]
            else:
                claims=validate(report.get('claim_support'),report.get('targets',[]),known[report['id']],evidence)
                if claims is None or references(claims)!=set(report['reference_ids']):continue
            # A new audit contract is not permission to bypass an operator hold.
            if citation_audit and operator_held(known[report['id']],report,evidence):
                deferred_components.append({'id':report['id'],'reason':'operator_evidence_hold'})
                continue
            key=component_key(requests,report,evidence,citation_audit=citation_audit)
            saved=saved_component(folder,key,report['id'],pin,decisions,citation_audit=citation_audit)
            if saved is not None:
                cached_count+=1
                if saved:retained.append(report['id'])
                continue
            if len(selected)>=MAX_REPORTS:
                deferred_components.append({'id':report['id'],'reason':'batch_report_limit'})
                continue
            if sum(len(r.get('claim_support',[])) for r in selected)+len(claims)>MAX_CLAIMS:
                deferred_components.append({'id':report['id'],'reason':'batch_claim_limit'})
                continue
            trial,payload,wire=_wire(requests,[*selected,report],evidence,citation_audit)
            # Pack whole request components with shared full sources, never cut
            # a quote's surrounding evidence to fit a monetary ceiling.
            if len(json.dumps(wire,ensure_ascii=False).encode('utf-8'))<=limit:
                selected=trial;messages=wire;component_keys.append(key)
            else:
                _,single_payload,single_wire=_wire(requests,[report],evidence,citation_audit)
                size=len(json.dumps(single_wire,ensure_ascii=False).encode('utf-8'))
                deferred_components.append({'id':report['id'],
                    'reason':'component_exceeds_size_limit' if size>limit else 'batch_size_limit',
                    'message_bytes':size,'limit_bytes':limit})
    except Exception as exc:
        return retained, {'status':'pending','error_type':type(exc).__name__,'model_calls':0}
    deferred=len(eligible)-len(selected)-cached_count
    if not selected:
        return retained, {'status':'reviewed' if affected.issubset(retained) else 'pending','deferred_count':deferred,
                          'deferred_components':deferred_components,
                          'cached_component_count':cached_count,'model_calls':0,'decisions':decisions}
    identity={'version':version,'messages':_digest(messages)}
    path=folder/(_digest(identity)+'.json')
    from backup_files import backup_lock
    calls=0
    try:
        with backup_lock(folder,'components.lock'):
            if any((folder/('component-'+key+'.json')).exists() for key in component_keys):
                raise ValueError('claim_component_already_claimed')
            if path.exists():
                state=json.loads(path.read_text('utf-8'))
                if state.get('identity')!=identity or 'raw' not in state: raise ValueError('claim_review_requires_attention')
                if state.get('source_pin')!=pin:raise ValueError('claim_review_source_changed')
                if state.get('raw_digest')!=_digest(state['raw']): raise ValueError('claim_review_output_changed')
            else:
                state={'identity':identity,'status':'started','source_pin':pin,
                       'reports':selected,'component_keys':component_keys}
                if citation_audit:
                    # Rebuild only the admitted batch (the last trial may be deferred).
                    _, final_payload, _ = _wire(requests, selected, evidence, True)
                    state.update(citation_payload=final_payload,citation_payload_digest=_digest(final_payload))
                def on_start(source_id,current_pin):
                    nonlocal calls
                    if current_pin!=pin:raise ValueError('claim_review_source_changed_before_request')
                    check_current_private_sources(evidence)
                    check_baseline_sources([marked[r['id']] for r in selected])
                    state['source_id']=source_id;atomic_write(path,state)
                    for key in component_keys:atomic_write(folder/('component-'+key+'.json'),{'batch':path.stem})
                    calls=1
                result=await provider(messages,on_start)
                state.update(status=result.status,usage=result.usage,http_status=result.http_status)
                # Preserve returned text even when parsing failed. A saved error
                # remains attention-required; saving it does not approve/retry it.
                if result.raw_content is not None:
                    state.update(raw=result.raw_content,raw_digest=_digest(result.raw_content))
                atomic_write(path,state)
                if result.status!='ok':raise ValueError('claim_review_provider_failed')
            check_current_private_sources(evidence)
            check_baseline_sources([marked[r['id']] for r in selected])
            assessment=_assessment(state,citation_audit)
            accepted=assessment['approved_ids']
            return retained+accepted, {'status':'reviewed','approved_count':len(accepted),'report_count':len(selected),
                                      'deferred_components':deferred_components,
                                      'deferred_count':deferred,'model_calls':calls,'usage':state.get('usage',{}),
                                      'decisions':decisions+assessment['decisions']}
    except Exception as exc:
        return retained, {'status':'pending','error_type':type(exc).__name__,'model_calls':calls,
                          'deferred_components':deferred_components}
