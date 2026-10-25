"""Read-only legacy Markdown archive, independent of the retired OB engine.

No OB imports, configuration loader, embeddings, activation, decay or writes.
Old summaries remain attributed derived records, not new V2 event facts.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import frontmatter


class LegacyMemoryArchive:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.read_errors: dict[str, int] = {}

    def _error(self, kind: str) -> None:
        self.read_errors[kind] = self.read_errors.get(kind, 0) + 1

    def _safe_path(self, path: Path) -> bool:
        try:
            info = path.lstat()
            # Junctions are not necessarily is_symlink() on Windows.
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                self._error("UnsafePath")
                return False
            if not path.resolve().is_relative_to(self.root):
                self._error("UnsafePath")
                return False
            return True
        except OSError as exc:
            self._error(type(exc).__name__)
            return False

    def _markdown_paths(self, directory: Path):
        def failed(exc):
            self._error(type(exc).__name__)
        for current, dirs, files in os.walk(directory, topdown=True, followlinks=False, onerror=failed):
            parent = Path(current)
            dirs[:] = sorted(name for name in dirs if self._safe_path(parent / name))
            for name in sorted(files):
                path = parent / name
                if path.suffix.lower() == ".md" and self._safe_path(path):
                    yield path

    def list_buckets(self, *, include_archive: bool = True) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            raise FileNotFoundError("legacy archive is unavailable")
        result = []
        seen = set()
        self.read_errors = {}
        for folder in ("permanent", "dynamic", "archive"):
            if folder == "archive" and not include_archive:
                continue
            directory = self.root / folder
            if not directory.exists():
                continue
            if not self._safe_path(directory) or not directory.is_dir():
                continue
            for path in self._markdown_paths(directory):
                try:
                    post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
                except Exception as exc:
                    kind = type(exc).__name__
                    self._error(kind)
                    continue
                metadata = dict(post.metadata)
                bucket_id = str(metadata.get("id") or path.stem)
                if bucket_id in seen:
                    continue
                seen.add(bucket_id)
                result.append({"id": bucket_id, "metadata": metadata,
                               "content": post.content, "archive_folder": folder})
        return result

    def get(self, bucket_id: str) -> dict | None:
        clean = str(bucket_id or "").removeprefix("ob_rev_")
        item = next((item for item in self.list_buckets() if item["id"] == clean), None)
        if item is None and self.read_errors:
            raise RuntimeError("legacy archive lookup is incomplete")
        return item

    def profile_metadata(self) -> dict:
        return next((item["metadata"] for item in self.list_buckets()
                     if item["metadata"].get("type") == "profile"
                     or "user_profile" in (item["metadata"].get("tags") or [])), {})

    def observation(self) -> dict:
        buckets = self.list_buckets()
        counts = {key: sum(item["archive_folder"] == key for item in buckets)
                  for key in ("permanent", "dynamic", "archive")}
        return {"status": "read_only_archive", "runtime": "retired",
                "writable": False, "total": len(buckets), "bucket_counts": counts,
                "complete": not self.read_errors,
                "read_error_counts": dict(self.read_errors)}


def get_legacy_archive() -> LegacyMemoryArchive:
    # Archives are private data, independent of the retired engine's checkout.
    # Explicit historical locations remain supported without loading OB code.
    default = Path(__file__).resolve().parents[1] / "data" / "legacy_memory_archive" / "buckets"
    explicit = os.environ.get("OB_REV_BUCKETS_DIR")
    old_root = os.environ.get("OB_REV_PATH")
    return LegacyMemoryArchive(explicit or (Path(old_root) / "buckets" if old_root else default))


async def load_legacy_buckets(*, include_archive: bool = True) -> list[dict]:
    import asyncio
    return await asyncio.to_thread(get_legacy_archive().list_buckets,
                                   include_archive=include_archive)


async def load_legacy_bucket(bucket_id: str) -> dict | None:
    import asyncio
    return await asyncio.to_thread(get_legacy_archive().get, bucket_id)
