"""One local snapshot lifecycle: seven daily points, two pre-change points / 48h."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import csv
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import subprocess
import time
import uuid

from backup_files import BackupError, backup_lock, plain_path, tree_files, verify_files, remove_candidate, digest
from backup_sources import sources, DATABASE_SUFFIXES
from backup_legacy import read_legacy, RUNTIME_NAME
from sqlite_backup import backup_sqlite

MANIFEST = 'backup_manifest.json'
PRODUCER = 'mirrow_backup'
NAME = re.compile(r'(daily|prechange)_(\d{4}-\d{2}-\d{2})_(\d{6})_([0-9a-f]{32})')
PENDING = '.pending_unified_'
LOCK = '.daily_backup.lock'  # Retain the existing scheduled-job lock during the transition.
DEFAULT_ROOT = Path('./MIRROW_backup')
OPERATION_KINDS = {'world_retry', 'other_preview'}


def _prune_artifacts(root, kind, now):
    candidates, failures = [], []
    for path in root.iterdir():
        if not path.name.startswith(kind + '_') or not path.is_dir():
            continue
        marker = path / '.backup_lifecycle.json'
        if not marker.exists():
            continue  # Historical artifacts need the one-time review.
        try:
            plain_path(marker)
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data.get('producer') == 'mirrow_operation_artifacts' and data.get('name') == path.name and data.get('kind') == kind:
                started = datetime.fromisoformat(data['started_at'])
                if started.tzinfo is not None:
                    raise BackupError('invalid_artifact_time')
                candidates.append((started, path))
        except (BackupError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            failures.append(dict(path=str(path), reason=type(exc).__name__))
    for index, (started, path) in enumerate(sorted(candidates, reverse=True)):
        if started <= now and (index >= 2 or now-started > timedelta(hours=48)):
            try:
                remove_candidate(root, path)
            except (BackupError, OSError) as exc:
                failures.append(dict(path=str(path), reason=type(exc).__name__))
    return failures


@contextmanager
def operation_artifacts(root, kind):
    """Bound existing domain rollback/diagnostic layouts without changing their consumers."""
    if kind not in OPERATION_KINDS:
        raise ValueError('Unknown operation artifact kind')
    root = plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with backup_lock(root, '.' + kind + '.lock'):
        now = datetime.now()
        _prune_artifacts(root, kind, now)
        target = root / f'{kind}_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}'
        target.mkdir()
        _private_directory(target)
        data = dict(producer='mirrow_operation_artifacts', kind=kind, name=target.name, started_at=now.isoformat())
        (target / '.backup_lifecycle.json').write_text(json.dumps(data), encoding='utf-8')
        try:
            yield target
        finally:
            _prune_artifacts(root, kind, datetime.now())


def clean_operation_artifacts(repo):
    root = plain_path(Path(repo) / 'ai-chat-backend/backups')
    if not root.exists():
        return []
    failures = []
    for kind in OPERATION_KINDS:
        try:
            with backup_lock(root, '.' + kind + '.lock'):
                failures += _prune_artifacts(root, kind, datetime.now())
        except BackupError as exc:
            if str(exc) != 'backup_busy':
                failures.append(dict(path=str(root), reason=str(exc)))
    return failures


def _private_directory(path):
    """Local snapshots contain credentials; do not inherit C-drive read permissions."""
    if os.name == 'nt':
        identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True, capture_output=True)
        # Only the ASCII SID is used; localized account names need not match Python's locale.
        sid = next(csv.reader(identity.stdout.decode('utf-8', errors='replace').strip().splitlines()))[-1]
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                        f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
                       check=True, capture_output=True)
    else:
        path.chmod(0o700)


def _signature(directory):
    return tuple((name, (directory / name).stat().st_size,
                  (directory / name).stat().st_mtime_ns)
                 for name in sorted(tree_files(directory)))


def verified(root, directory):
    root, directory = plain_path(root), plain_path(directory)
    match = NAME.fullmatch(directory.name)
    if directory.parent != root or not (match or RUNTIME_NAME.fullmatch(directory.name)) or not directory.is_dir():
        raise BackupError('unmanaged_snapshot')
    data = json.loads((plain_path(directory / MANIFEST)).read_text(encoding='utf-8'))
    if isinstance(data, dict) and data.get('format_version') in {1, 2}:
        item = read_legacy(directory, data, match)
        item['signature'] = _signature(directory)
        return item
    if (not isinstance(data, dict) or data.get('producer') != PRODUCER or type(data.get('format_version')) is not int
            or data['format_version'] != 3 or data.get('status') != 'complete'
            or data.get('kind') != match[1] or data.get('backup_name') != directory.name
            or data.get('snapshot_id') != match[4]
            or data.get('database_consistency') != 'sqlite_online_backup'
            or data.get('cross_source_atomic') is not False):
        raise BackupError('unverified_manifest')
    if (not isinstance(data.get('present_sources'), list)
            or not all(isinstance(name, str) for name in data['present_sources'])
            or not isinstance(data.get('absent_sources'), list)
            or not all(isinstance(name, str) for name in data['absent_sources'])
            or (match[1] == 'prechange' and not isinstance(data.get('operation'), str))):
        raise BackupError('unverified_source_catalog')
    stamp = datetime.strptime(match[2] + match[3], '%Y-%m-%d%H%M%S')
    completed = datetime.fromisoformat(data['completed_at'])
    if completed.tzinfo is not None or completed < stamp:
        raise BackupError('invalid_completion_time')
    _, size = verify_files(directory, data)
    return dict(path=directory, stamp=stamp, completed=completed, kind=match[1],
                operation=data.get('operation'), size=size, signature=_signature(directory))


def _scan(root, now):
    valid, failures = [], []
    for path in root.iterdir():
        if not (NAME.fullmatch(path.name) or RUNTIME_NAME.fullmatch(path.name)):
            continue  # Unknown/manual packages require the one-time migration audit.
        try:
            item = verified(root, path)
            if item['completed'] > now:
                raise BackupError('future_snapshot')
            valid.append(item)
        except (BackupError, OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(dict(path=str(path), reason=str(exc) if isinstance(exc, BackupError) else type(exc).__name__))
    return valid, failures


def _rotate(root, valid, now, current=None):
    """Consume this run's verification results once, then check identity before removal."""
    cutoff = now.date() - timedelta(days=6)
    days, short = set(), 0
    keep, remove = [], []
    def scope_rank(item):
        return {'daily_v1': 1, 'runtime_v2': 2}.get(item.get('legacy'), 3)
    valid = sorted(valid, key=lambda item: (item['path'] == current, item['stamp'].date(), scope_rank(item), item['completed'], item['path'].name), reverse=True)
    # Never discard the last good daily recovery point, even if a machine was off for >7 days.
    daily = [item for item in valid if item['kind'] == 'daily']
    full = [item['path'] for item in sorted(daily, key=lambda i: (scope_rank(i), i['completed']), reverse=True)]
    last_daily = next(iter(full), next((item['path'] for item in valid if item['kind'] == 'daily'), None))
    for item in valid:
        if item['kind'] == 'daily':
            date = item['stamp'].date()
            # Old partial daily copies preserve complementary book/agent-memory history until expiry.
            slot = (date, item.get('legacy') == 'daily_v1')
            retain = item['path'] == last_daily or (date >= cutoff and slot not in days)
            if retain:
                days.add(slot)
        else:
            retain = item['path'] == current or (short < 2 and item['stamp'] >= now - timedelta(hours=48))
            if retain:
                short += 1
        (keep if retain else remove).append(item)
    failures, released, removed = [], 0, []
    anchor = next((item for item in keep if item['path'] == last_daily), None)
    if anchor and remove:
        try:
            if _signature(anchor['path']) != anchor['signature']:
                raise BackupError('recovery_anchor_changed')
        except (BackupError, OSError):
            return valid, dict(removed=0, removed_paths=[], released_bytes=0,
                               failures=[dict(path=str(anchor['path']), reason='recovery_anchor_changed')])
    for item in remove:
        try:
            if _signature(item['path']) != item['signature']:
                raise BackupError('changed_snapshot')
            remove_candidate(root, item['path'])
            released += item['size']
            removed.append(str(item['path']))
        except (BackupError, OSError) as exc:
            failures.append(dict(path=str(item['path']), reason=type(exc).__name__))
            keep.append(item)
    return keep, dict(removed=len(removed), removed_paths=removed, released_bytes=released, failures=failures)


def _pending_cleanup(root, now):
    failures = []
    for path in root.iterdir():
        if path.name.startswith(PENDING):
            try:
                if now.timestamp() - path.stat().st_mtime > 24 * 3600:
                    remove_candidate(root, path)
            except (BackupError, OSError) as exc:
                failures.append(dict(path=str(path), reason=type(exc).__name__))
    return failures


def _create_locked(repo, root, memory, kind, operation, now):
    pending_failures = _pending_cleanup(root, now)
    valid, scan_failures = _scan(root, now)
    valid, before = _rotate(root, valid, now)
    # A caller supplies a unique operation identity; retries reuse the same pre-change image.
    reused = next((i for i in valid if kind == 'prechange' and i['kind'] == kind and i['operation'] == operation), None)
    if reused:
        return dict(path=str(reused['path']), reused=True, total_bytes=reused['size'],
                    **{**before, 'failures': pending_failures + scan_failures + before['failures']})
    files, present, absent = sources(repo, memory)
    if not files:
        raise BackupError('no_source_files')
    # Previously present source roots cannot silently disappear on a broken mount/path.
    prior = next((i for i in valid if i['kind'] == 'daily'), None)
    if prior and not prior.get('legacy'):
        prior_data = json.loads((prior['path'] / MANIFEST).read_text(encoding='utf-8'))
        missing = set(prior_data['present_sources']) - set(present)
        same_catalog = set(prior_data['present_sources'] + prior_data['absent_sources']) == set(present + absent)
        if same_catalog and missing:
            raise BackupError('previous_source_unavailable')
    identity = uuid.uuid4().hex
    name = f'{kind}_{now:%Y-%m-%d_%H%M%S}_{identity}'
    final = root / name
    pending = Path(tempfile.mkdtemp(prefix=PENDING, dir=root))
    start = time.monotonic()
    try:
        _private_directory(pending)
        entries = []
        for source, relative, file_kind in files:
            source = plain_path(source)
            target = pending / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if file_kind == 'sqlite':
                backup_sqlite(source, target)
            else:
                shutil.copy2(source, target)
            # Settings and canonical session JSON are execution-critical structured files.
            if relative == 'repo/ai-chat-backend/settings.json' or (str(Path(relative).parent).replace('\\', '/') == 'repo/ai-chat-backend/sessions' and target.suffix == '.json'):
                json.loads(target.read_text(encoding='utf-8-sig'))
            entries.append(dict(path=relative, bytes=target.stat().st_size, sha256=digest(target)))
        manifest = dict(producer=PRODUCER, format_version=3, status='complete', kind=kind,
                        snapshot_id=identity, backup_name=name, operation=operation,
                        started_at=now.isoformat(), completed_at=(now + timedelta(seconds=time.monotonic()-start)).isoformat(),
                        present_sources=present, absent_sources=absent, files=entries,
                        file_count=len(entries), total_bytes=sum(e['bytes'] for e in entries),
                        database_consistency='sqlite_online_backup', cross_source_atomic=False)
        (pending / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        verify_files(pending, manifest)
        pending.rename(final)
    finally:
        if pending.exists():
            remove_candidate(root, pending)
    item = dict(path=final, stamp=now.replace(microsecond=0), completed=datetime.fromisoformat(manifest['completed_at']),
                kind=kind, operation=operation, size=manifest['total_bytes'], signature=_signature(final))
    _, after = _rotate(root, valid + [item], now, final)
    return dict(path=str(final), total_bytes=item['size'], reused=False,
                removed=before['removed']+after['removed'], removed_paths=before['removed_paths']+after['removed_paths'],
                released_bytes=before['released_bytes']+after['released_bytes'],
                failures=pending_failures+scan_failures+before['failures']+after['failures'])


def create_backup(repo, root, memory, *, kind='daily', operation=None, now=None):
    if kind not in {'daily', 'prechange'} or (kind == 'prechange' and not operation):
        raise ValueError('Pre-change snapshots require a unique operation identity')
    root = plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with backup_lock(root, LOCK):
        artifact_failures = clean_operation_artifacts(repo)
        result = _create_locked(repo, root, memory, kind, operation, now or datetime.now())
        result['failures'] += artifact_failures
        return result


@contextmanager
def before_change(repo, root, memory, operation):
    """Hold the same lock through the data operation so its recovery point cannot expire."""
    if not operation:
        raise ValueError('A unique operation identity is required')
    root = plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with backup_lock(root, LOCK):
        result = _create_locked(repo, root, memory, 'prechange', operation, datetime.now())
        yield result['path']


def rotate_backups(root, *, now=None):
    root = plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    with backup_lock(root, LOCK):
        now = now or datetime.now()
        pending_failures = _pending_cleanup(root, now)
        valid, failures = _scan(root, now)
        _, result = _rotate(root, valid, now)
        result['failures'] += pending_failures + failures
        return result


def restore_backup(directory, target):
    """Recover into a new private directory only; never overwrite a running installation."""
    directory, target = plain_path(directory), plain_path(target)
    root = directory.parent
    if target.exists() or target.is_relative_to(root):
        raise BackupError('restore_target_must_be_new_and_outside_backup_root')
    with backup_lock(root, LOCK):
        item = verified(root, directory)
        if item.get('legacy'):
            raise BackupError('legacy_snapshot_keeps_original_restore_layout')
        data = json.loads((directory / MANIFEST).read_text(encoding='utf-8'))
        target.mkdir(parents=True)
        _private_directory(target)
        for entry in data['files']:
            dest = target / entry['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(directory / entry['path'], dest)
        shutil.copy2(directory / MANIFEST, target / MANIFEST)
        verify_files(target, data)
        databases = 0
        for entry in data['files']:
            path = target / entry['path']
            if path.suffix.lower() in DATABASE_SUFFIXES:
                db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                try:
                    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                        raise BackupError('restored_database_check_failed')
                finally:
                    db.close()
                databases += 1
    return dict(target=str(target), files=data['file_count'], databases=databases, verified=True)
