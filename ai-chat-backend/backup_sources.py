"""Explicit local recovery contents; code and installed dependencies belong to Git/installers."""
import os
from pathlib import Path
from backup_files import BackupError, plain_path
DATABASE_SUFFIXES = {'.db', '.sqlite', '.sqlite3'}
BACKEND_SOURCES = ('sessions', 'events', 'data', 'conversations/logs', 'conversations/index', 'silicon_perception/data', 'static', 'self_book/entries', 'other_book/entries', 'world_book', 'voice_manager/output', 'settings.json', 'keyword_registry.yaml', '.env')
EXCLUDED = {'events/backups', 'events/memory_v2/backups'}

def sources(repo, memory):
    repo, memory = (plain_path(repo), plain_path(memory))
    backend = repo / 'ai-chat-backend'
    configured = [(backend / rel, f'repo/ai-chat-backend/{rel}') for rel in BACKEND_SOURCES]
    configured += [(repo / rel, f'repo/{rel}') for rel in ('CLAUDE.md', 'AGENTS.md')]
    configured += [(memory / rel, f'agent_memory/{rel}') for rel in ('MEMORY.md', 'memory')]
    result, absent, present = ([], [], [])

    def add(path, relative):
        plain_path(path)
        if relative in {'repo/ai-chat-backend/events/bookshelf_search_vectors.npz', 'repo/ai-chat-backend/events/bookshelf_search_vectors.tmp.npz'}:
            return
        if path.parent == backend / 'voice_manager/output' and path.name.startswith('tts_'):
            return
        for suffix in ('-wal', '-shm', '-journal'):
            if path.name.endswith(suffix):
                db = path.with_name(path.name[:-len(suffix)])
                if db.suffix.lower() in DATABASE_SUFFIXES and db.is_file():
                    return
        kind = 'sqlite' if path.suffix.lower() in DATABASE_SUFFIXES else 'file'
        result.append((path, relative, kind))
    for path, relative in configured:
        path = plain_path(path)
        try:
            path.stat()
        except FileNotFoundError:
            absent.append(relative)
            continue
        present.append(relative)
        if path.is_file():
            add(path, relative)
        elif path.is_dir():

            def failed(error):
                raise error
            for base, dirs, files in os.walk(path, followlinks=False, onerror=failed):
                for name in dirs + files:
                    plain_path(Path(base) / name)
                dirs[:] = [name for name in dirs if name != '__pycache__' and (Path(base) / name).relative_to(backend).as_posix() not in EXCLUDED] if path.is_relative_to(backend) else [n for n in dirs if n != '__pycache__']
                for name in sorted(files):
                    item = Path(base) / name
                    add(item, relative + '/' + item.relative_to(path).as_posix())
        else:
            raise BackupError('invalid_source_file')
    if len({name for _, name, _ in result}) != len(result):
        raise BackupError('duplicate_source')
    return (result, present, absent)
