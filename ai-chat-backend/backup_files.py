"""Local snapshot file, path and locking contracts shared by backup entry points."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

class BackupError(RuntimeError):
    pass


def plain_path(path):
    """Reject links/junctions in the path, including existing parent components."""
    path = Path(path).absolute()
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BackupError('reparse_path')
    return path.resolve()


@contextmanager
def backup_lock(root, lock_name='.daily_backup.lock'):
    path = plain_path(root / lock_name)
    if path.parent != plain_path(root):
        raise BackupError('invalid_lock_path')
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BackupError('backup_busy') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def tree_files(directory, *, allowed_dirs=None):
    plain_path(directory)
    result = set()
    def failed(error):
        raise error
    for base, dirs, files in os.walk(directory, followlinks=False, onerror=failed):
        for name in dirs + files:
            plain_path(Path(base) / name)
        if allowed_dirs is not None:
            for name in dirs:
                if (Path(base) / name).relative_to(directory).as_posix() not in allowed_dirs:
                    raise BackupError('changed_snapshot_tree')
        for name in files:
            path = Path(base) / name
            result.add(path.relative_to(directory).as_posix())
    return result


def remove_candidate(root, directory):
    root, directory = plain_path(root), plain_path(directory)
    if directory.parent != root:
        raise BackupError('unsafe_removal')
    tree_files(directory)
    shutil.rmtree(directory)


def verify_files(directory, data, manifest_name="backup_manifest.json"):
    entries = data.get('files')
    if not isinstance(entries, list) or not entries or type(data.get('file_count')) is not int or data['file_count'] != len(entries):
        raise BackupError('unverified_files')
    expected, expected_dirs, total = set(), set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError('unverified_file')
        name = entry.get('path')
        if not isinstance(name, str) or '\\' in name or ':' in name:
            raise BackupError('invalid_file_path')
        parts = PurePosixPath(name)
        if parts.is_absolute() or name != parts.as_posix() or '..' in parts.parts or not parts.parts or name == manifest_name or name in expected:
            raise BackupError('invalid_file_path')
        expected.add(name)
        expected_dirs.update(parent.as_posix() for parent in parts.parents if parent.as_posix() != '.')
        path = plain_path(directory / name)
        if not path.is_relative_to(directory) or not path.is_file():
            raise BackupError('invalid_file_path')
        size = entry.get('bytes')
        if type(size) is not int or size < 0 or path.stat().st_size != size or digest(path) != entry.get('sha256'):
            raise BackupError('changed_snapshot')
        total += size
    if type(data.get('total_bytes')) is not int or data['total_bytes'] != total or tree_files(directory, allowed_dirs=expected_dirs) != expected | {manifest_name}:
        raise BackupError('changed_snapshot_tree')
    return frozenset(expected), total


def file_records(directory):
    entries = [dict(path=name, bytes=(directory / name).stat().st_size,
                    sha256=digest(directory / name)) for name in sorted(tree_files(directory))]
    return entries, sum(entry['bytes'] for entry in entries)
