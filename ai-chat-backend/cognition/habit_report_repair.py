"""One missing-report repair inside the existing factual maintenance run.

No proposals, approvals, book writes, or independent habit authority originate here.
The caller persists the attempt before sending; interrupted attempts are not replayed.
"""
from . import habit_review
from .prompt_request import CognitionPrompt
from llm_usage_scope import usage_scope

CONTRACT = habit_review.EXTRACTION + '''
本轮只补齐 habit_review_requests 中缺失的复核报告，输出单个 JSON 对象 {"habit_reviews":[...]}。
existing_proposals 是前一阶段已经生成的提议，供判断本轮是否已有对应修订；本轮不生成或改写 proposals，也不作最终批准。
各报告保留原请求 ID 与 messages 的引用编号。已有报告由程序保存，不在本轮重新判断。
updated 仍只是修订判断，实际完成由后续独立审核及入书收据证明，不能把需要修订但未入书的认识写成已完成。
'''


async def repair(state, requests, space, snapshot, payload, llm, checkpoint, *,
                 source_date, run_id, shared_material_keys):
    existing = state.get('habit_review_reports', [])
    seen = {row['id'] for row in existing}
    missing = [row for row in requests if row['id'] not in seen]
    if not missing:
        return
    # This marker also survives provider failure or cancellation: at most one
    # additional call for this run, not a new automatic retry loop.
    attempt = state.get('habit_report_repair')
    if attempt is not None and ('material' not in attempt or 'raw' not in attempt.get('output', {})):
        return  # Old/unknown attempts never reopen their paid budget.
    if attempt is None:
        attempt = {'status': 'started', 'requested_ids': [r['id'] for r in missing],
                   'recovered_count': 0,
                   'material': {'requests': missing, 'space': space, 'snapshot': snapshot, 'payload': payload}}
        state['habit_report_repair'] = attempt
        checkpoint()
    frozen = attempt['material']
    missing, space, snapshot, payload = frozen['requests'], frozen['space'], frozen['snapshot'], frozen['payload']
    n_of = {row['id']: n for n, row in enumerate(space, 1)}
    target_catalogue = []
    missing_payload = {**payload, 'habit_review_requests': habit_review.model_requests(missing, n_of, local=True,
        snapshot=snapshot, target_catalogue=target_catalogue)}
    if target_catalogue:
        missing_payload['habit_target_choices'] = target_catalogue
    try:
        with usage_scope('cognition_world_extract'):
            from .habit_claim_support import GUIDE
            contract = CONTRACT + (GUIDE if any(r.get('support_contract') for r in missing) else '')
            from .habit_local_contract import enabled
            if any(enabled(r) for r in missing):
                contract = habit_review.extraction_contract(missing) + (
                    '\n本轮只补齐所列请求的 habit_reviews；existing_proposals 是前阶段提议，'
                    '本轮不生成新 proposals。程序保留已有有效报告。')
            from .raw_output import capture
            raw = await capture(attempt, 'output', CognitionPrompt(contract, missing_payload, stage='habit_report_repair',
                source_date=source_date, run_id=run_id, shared_material_keys=shared_material_keys), llm, checkpoint)
        assessment = habit_review.report_assessment(raw, missing, space, snapshot)
        recovered = assessment['reports']
        state['habit_review_reports'] = [*existing, *(r for r in recovered if r['id'] not in seen)]
        attempt.update(status='completed' if len(recovered) == len(missing) else 'incomplete',
                       recovered_count=len(recovered), rejections=assessment['rejections'])
    except Exception as exc:
        # Fact proposals are already checkpointed; an optional report repair
        # cannot erase them or turn a missing report into a successful one.
        attempt.update(status='failed', error_type=type(exc).__name__)
    checkpoint()
