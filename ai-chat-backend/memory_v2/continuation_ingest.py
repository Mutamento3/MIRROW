"""Low-frequency continuation-only lane; no batch encoding or scene reconstruction."""
from .day_settlement import DAY_SETTLEMENT_VERSION
from .day_settlement_runner import day_settlement_prompt_version
from .day_settlement_experiment import FlashDaySettlementModelAdapter
from .continuation_pages import run_continuation_pages


async def run_continuation_day(store, *, active_date, source_namespace="mainline", max_model_calls=1,
                               explicit_retry=False, model_call=None, completed_before_active_date=None):
    receipt = store.get_day_settlement_receipt_for_day(source_namespace=source_namespace,
        active_date=active_date, settlement_version=DAY_SETTLEMENT_VERSION,
        prompt_version=day_settlement_prompt_version(active_date))
    if receipt is None:
        return {"status": "continuation_missing_day_receipt", "request_count": 0,
                "temporary_commit_valid": False}
    replan = {"status": "not_needed"}
    if completed_before_active_date is not None and active_date in store.list_continuation_replan_dates(
            source_namespace=source_namespace, before_active_date=completed_before_active_date):
        from .continuation_replan import prepare_continuation_replan
        try:
            replan = prepare_continuation_replan(store, active_date=active_date,
                source_namespace=source_namespace, completed_before_active_date=completed_before_active_date)
        except Exception as exc:
            return {"status": "continuation_replan_error", "request_count": 0, "temporary_commit_valid": False,
                    "error_code": type(exc).__name__}
    adapter = model_call or FlashDaySettlementModelAdapter(usage_tag="memory_v2_continuation_pages")
    result = await run_continuation_pages(store, settlement_id=receipt["id"], model_call=adapter,
                                        max_model_calls=max_model_calls, explicit_retry=explicit_retry)
    return {"status": "ok" if result.successful else "continuation_" + result.status,
            "temporary_commit_valid": result.committed_page_count > 0 or result.status == 'complete',
            "request_count": result.model_call_count, "continuation_report": result.safe_dict(),
            "replan_report": replan,
            "usage": adapter.safe_usage() if hasattr(adapter, "safe_usage") else {}}
