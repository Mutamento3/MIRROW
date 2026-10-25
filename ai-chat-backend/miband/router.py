from datetime import datetime, timedelta
import logging

from fastapi import APIRouter

from .models import Batch

from .store import TZ, ingest, overview

router = APIRouter(prefix='/mi-band', tags=['mi-band'])
logger = logging.getLogger(__name__)


@router.post('/activity-batch')
def activity_batch(body: Batch):
    result = ingest(body)
    _refresh_daily_sleep(body)
    return result


@router.get('')
def status():
    return overview()


def _refresh_daily_sleep(body: Batch) -> None:
    """Project completed band nights into the existing daily summary authority."""
    sleep_samples = [
        sample for sample in body.samples
        if sample.raw_kind == 120 or sample.sleep_stage
    ]
    if not sleep_samples:
        return
    dates = set()
    for sample in sleep_samples:
        local_date = datetime.fromtimestamp(sample.measured_at, TZ).date()
        # Sessions are attributed to their end date and can cross midnight.
        dates.add(local_date)
        dates.add(local_date + timedelta(days=1))
    try:
        from silicon_perception.analysis.daily_aggregator import DailyAggregator
        from silicon_perception.analysis.behavior_profile import get_behavior_profile
        from silicon_perception.recording.health_store import get_store

        aggregator = DailyAggregator(get_store())
        changed = False
        today = datetime.now(TZ).date()
        for date in sorted(value for value in dates if value <= today):
            fields = aggregator.project_miband_sleep_date(str(date))
            changed = changed or bool(fields.get('sleep_min'))
        if changed:
            get_behavior_profile().invalidate()
    except Exception as exc:
        # Ingestion remains authoritative even if its derived daily projection
        # is temporarily unavailable; the next batch or explicit aggregation
        # can rebuild it from miband.db.
        logger.warning("MiBand 睡眠日汇总投影失败: %s", exc)
        return
