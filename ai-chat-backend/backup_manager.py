"""Compatibility for old loaded lifespan handlers; scheduled backups use backup_service."""
import logging
from pathlib import Path
from backup_service import create_backup, DEFAULT_ROOT

logger = logging.getLogger(__name__)

def should_skip_startup_backup(*args, **kwargs):
    return True

def create_full_backup(prefix='shutdown'):
    if prefix in {'startup', 'shutdown'}:
        logger.info('Legacy lifespan backup retired; scheduled backup owns the lifecycle')
        return None
    repo = Path(__file__).resolve().parents[1]
    result = create_backup(repo, DEFAULT_ROOT, Path.home()/'.claude/projects/c--MIRROW',
                           kind='prechange', operation=prefix)
    return result['path']

def create_session_backup(prefix='hourly'):
    raise RuntimeError('Hourly backup retired; use backup_service.before_change for data operations')

def rotate_backups():
    # A running process may still have the old shutdown callback loaded.
    return 0
