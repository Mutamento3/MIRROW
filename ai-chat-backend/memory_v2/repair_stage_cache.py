"""Identity-bound private encoding checkpoints, never production memory."""
from __future__ import annotations

import json
import os
import tempfile
import sqlite3
from contextlib import closing
from pathlib import Path

from .store import MemoryV2Store


def identity(plan):
    return {
        'version': 'repair-encoding-checkpoint-v1',
        'repair_id': plan.repair_id,
        'authority_digest': plan.authority_digest,
        'baseline_digest': plan.baseline_digest,
        'batches': [{
            'id': MemoryV2Store.resolve_batch_id(item.batch),
            'digest': item.batch.source_digest,
            'encoder': item.batch.encoder_version,
            'prompt': item.batch.prompt_version,
        } for item in plan.replay_day.batches],
    }


def resumable(directory: Path, plan) -> bool:
    """Only an unpromoted encoding prefix with the exact current plan may resume."""
    try:
        value = json.loads((directory / 'checkpoint.json').read_text('utf8'))
        return (value == {'identity': identity(plan), 'phase': 'encode'}
                and (directory / 'memory_v2.db').is_file())
    except (OSError, ValueError, TypeError):
        return False


def mark(directory: Path, plan, phase: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.checkpoint-')
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump({'identity': identity(plan), 'phase': phase}, stream)
        os.replace(temporary, directory / 'checkpoint.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as origin:
        with closing(sqlite3.connect(destination)) as target:
            origin.backup(target)
            target.commit()
            if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('encoding checkpoint integrity failed')


def freeze_encoding(directory: Path, plan) -> None:
    """Keep the validated encoding generation separate from mutable projections."""
    if not resumable(directory, plan):
        raise ValueError('encoding checkpoint identity mismatch')
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.encoding-', suffix='.db')
    os.close(fd)
    try:
        _backup(directory / 'memory_v2.db', Path(temporary))
        os.replace(temporary, directory / 'encoding_only.db')
        mark(directory, plan, 'replacement')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_encoding(directory: Path, plan) -> bool:
    """Restore only the exact private encoding snapshot, including after a crash."""
    try:
        value = json.loads((directory / 'checkpoint.json').read_text('utf8'))
    except (OSError, ValueError, TypeError):
        return False
    if (value != {'identity': identity(plan), 'phase': 'replacement'}
            or not (directory / 'encoding_only.db').is_file()):
        return False
    _backup(directory / 'encoding_only.db', directory / 'memory_v2.db')
    mark(directory, plan, 'encode')
    return True
