"""Read-only closure evidence for the derived scene recovery worker."""
from datetime import date, timedelta
from .day_repair import day_authority_digest
from .scene_planner import planner_for_ingest


def authority_binding(source, active_date):
    date.fromisoformat(active_date)
    return day_authority_digest(planner_for_ingest(source).plan_active_date(active_date))


def settled_binding(source, config, active_date):
    """A current complete formal receipt proves closure after process restart."""
    from .store import MemoryV2Store
    from .day_settlement import DAY_SETTLEMENT_VERSION
    from .day_settlement_runner import day_settlement_prompt_version
    from .day_settlement_assembler import DaySettlementPlanAssembler
    store = MemoryV2Store(config.memory_db_path, initialise=False)
    receipt = store.get_day_settlement_receipt_for_day(source_namespace='mainline',
        active_date=active_date, settlement_version=DAY_SETTLEMENT_VERSION,
        prompt_version=day_settlement_prompt_version(active_date))
    if receipt is None or not store.day_settlement_sources_current(str(receipt['id'])):
        return None
    plan = planner_for_ingest(source).plan_active_date(active_date)
    if not plan.batches or any(store.load_completed_events(batch.batch,
            batch_sources=batch.batch_sources,
            batch_source_validator=source.validate_batch_sources) is None for batch in plan.batches):
        return None
    assembly = DaySettlementPlanAssembler(config.memory_db_path).assemble(
        active_date=active_date,
        completed_before_active_date=(date.fromisoformat(active_date) + timedelta(days=1)).isoformat(),
        source_namespace='mainline')
    if (not assembly.ready or receipt['source_digest'] != assembly.source_digest
            or receipt['assembler_version'] != assembly.assembler_version):
        return None
    return day_authority_digest(plan)
