"""Read existing v1/v2 manifests during migration; never creates another backup format."""
from datetime import datetime
from pathlib import PurePosixPath
import re

from backup_files import BackupError, verify_files

RUNTIME_NAME = re.compile(r'([A-Za-z_]\w*)_(\d{4}-\d{2}-\d{2})_(\d{6})')


def read_legacy(directory, data, daily_match):
    version = data.get('format_version')
    runtime = RUNTIME_NAME.fullmatch(directory.name)
    if (version == 1 and type(version) is int and daily_match and daily_match[1] == 'daily'
            and data.get('producer') == 'mirrow_daily_backup'
            and data.get('snapshot_id') == daily_match[4] and data.get('date') == daily_match[2]):
        stamp = datetime.strptime(daily_match[2]+daily_match[3], '%Y-%m-%d%H%M%S')
        legacy = 'daily_v1'
    elif (version == 2 and type(version) is int and runtime
          and data.get('producer') == 'mirrow_runtime_backup' and data.get('kind') == runtime[1]):
        if runtime[1] == 'hourly':
            raise BackupError('legacy_light_requires_review')
        catalog = data.get('sources')
        if not isinstance(catalog, dict) or not catalog:
            raise BackupError('invalid_legacy_sources')
        for label, value in catalog.items():
            if (not isinstance(label, str) or not label.isidentifier() or not isinstance(value, str)
                    or '\\' in value or ':' in value or '..' in PurePosixPath(value).parts
                    or PurePosixPath(value).is_absolute() or value != PurePosixPath(value).as_posix()):
                raise BackupError('invalid_legacy_sources')
        stamp = datetime.strptime(runtime[2]+runtime[3], '%Y-%m-%d%H%M%S')
        legacy = 'runtime_v2'
    else:
        raise BackupError('unverified_manifest')
    completed = datetime.fromisoformat(data['completed_at'])
    if (data.get('status') != 'complete' or data.get('backup_name') != directory.name
            or data.get('database_consistency') != 'sqlite_online_backup'
            or data.get('cross_source_atomic') is not False
            or completed.tzinfo is not None or completed < stamp):
        raise BackupError('unverified_manifest')
    _, size = verify_files(directory, data)
    return dict(path=directory, stamp=stamp, completed=completed, kind='daily',
                operation=None, size=size, legacy=legacy)
