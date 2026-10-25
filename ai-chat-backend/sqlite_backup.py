"""SQLite snapshot primitive shared by backup entry points, without runtime imports."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import uuid

DATABASE_TIMEOUT_SECONDS = 120.0


def backup_sqlite(source: Path, destination: Path, *, timeout_seconds: float = DATABASE_TIMEOUT_SECONDS) -> None:
    """Snapshot committed data including WAL; publish one verified database atomically."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError('Source and backup must differ')
    if not source.is_file():
        raise FileNotFoundError('SQLite backup source unavailable')
    if timeout_seconds <= 0:
        raise ValueError('Backup timeout must be positive')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    deadline = time.monotonic() + timeout_seconds

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite backup deadline exceeded')

    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5.0)) as src:
            # Pin a WAL read snapshot so ongoing writes cannot restart every step.
            # Rollback-journal databases retain the existing per-step locking.
            if src.execute('PRAGMA journal_mode').fetchone()[0] == 'wal':
                src.execute('BEGIN')
                src.execute('SELECT rootpage FROM sqlite_schema LIMIT 1').fetchall()
            with closing(sqlite3.connect(str(temporary))) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                # Verification reads only the output; release the WAL reader now.
                src.rollback()
                if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise sqlite3.DatabaseError('SQLite backup verification failed')
        temporary.replace(destination)
    finally:
        # The only removed file is this invocation's uniquely named temporary output.
        if temporary.exists():
            temporary.unlink()
