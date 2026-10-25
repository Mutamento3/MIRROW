from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time
from typing import Any

import httpx

from .config import Settings
from .redaction import redact_text


ALLOWED_EXTENSIONS = {
    ".astro", ".bash", ".bat", ".c", ".cc", ".cfg", ".cpp", ".cs",
    ".css", ".cxx", ".dart", ".fish", ".go", ".gql", ".graphql", ".h",
    ".hcl", ".hh", ".hpp", ".html", ".hxx", ".ini", ".java", ".js",
    ".jsx", ".kt", ".kts", ".lua", ".md", ".php", ".proto", ".ps1",
    ".py", ".pyi", ".r", ".rb", ".rs", ".scala", ".sh", ".sql",
    ".svelte", ".swift", ".tf", ".toml", ".ts", ".tsx", ".vue", ".xml",
    ".yaml", ".yml", ".zsh", ".json",
}
ALLOWED_FILENAMES = {"cmakelists.txt", "dockerfile", "makefile"}
DENIED_PARTS = {
    ".cache", ".git", ".idea", ".mypy_cache", ".next", ".pytest_cache",
    ".tmp", ".tox", ".venv", ".venv-dml", ".vscode", "__pycache__",
    "backups", "buckets", "build", "captures", "conversations", "coverage",
    "data", "deriveddata", "dist", "events", "logs", "memory_index", "models",
    "model_cache", "node_modules", "pods", "sessions", "static", "target",
    "tracking_screenshots", "vendor",
}
REFERENCE_ALLOWED_PARTS = {"static"}
DENIED_NAMES = {".env", "settings.json", "config.yaml", "config.yml", "vault.dpapi", "package-lock.json"}
MAX_FILE_BYTES = 524_288
MAX_FALLBACK_ENTRIES = 50_000
FALLBACK_SEARCH_SECONDS = 1.0


def _allowed_source_name(name: str) -> bool:
    lowered_name = name.lower()
    sensitive_name = lowered_name.startswith(".env") or any(
        marker in lowered_name for marker in ("secret", "credential", "password", "cookie", "private_key", "vault")
    )
    suffix = Path(name).suffix.lower()
    allowed_type = lowered_name in ALLOWED_FILENAMES or suffix in ALLOWED_EXTENSIONS
    return lowered_name not in DENIED_NAMES and not sensitive_name and allowed_type


def _trust_for_root(root_name: str) -> str:
    return "untrusted_third_party" if root_name == "references" else "mirrow_source"


def _denied_parts_for_root(root_name: str) -> set[str]:
    if root_name == "references":
        return DENIED_PARTS - REFERENCE_ALLOWED_PARTS
    return DENIED_PARTS


class CodeAccess:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.roots = {
            "backend": settings.backend_root.resolve(),
            "frontend": (settings.repo_root / "ai-chat-frontend" / "src").resolve(),
            "references": (settings.repo_root / "reference-projects").resolve(),
        }
        self._rg_path = shutil.which("rg")
        self._health_cache: tuple[float, dict[str, Any]] | None = None
        self._heavy_read = asyncio.Semaphore(1)

    def _safe_file(self, root_name: str, relative: str) -> Path:
        root = self.roots.get(root_name)
        if root is None:
            raise ValueError("unknown code root")
        if not relative or ":" in relative or "\x00" in relative:
            raise ValueError("invalid code path")
        relative_path = Path(relative)
        if relative_path.is_absolute() or any(part in {".", ".."} for part in relative_path.parts):
            raise ValueError("invalid code path")
        raw_candidate = root / relative_path
        current = root
        for part in relative_path.parts:
            current = current / part
            if not current.exists():
                break
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("reparse/symlink targets are denied")
        candidate = raw_candidate.resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise ValueError("code path escaped the approved root")
        rel = candidate.relative_to(root)
        if any(part.lower() in _denied_parts_for_root(root_name) for part in rel.parts):
            raise ValueError("code path is denied")
        if not _allowed_source_name(candidate.name):
            raise ValueError("file type is denied")
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("code file is unavailable")
        if os.path.realpath(candidate) != str(candidate):
            raise ValueError("reparse/symlink targets are denied")
        return candidate

    async def read(self, root: str, path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        start_line = max(1, int(start_line))
        end_line = min(max(start_line, int(end_line)), start_line + 399)

        def load() -> dict[str, Any]:
            candidate = self._safe_file(root, path)
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            selected = lines[start_line - 1:end_line]
            text = "\n".join(f"{index}: {redact_text(line)}" for index, line in enumerate(selected, start_line))
            return {
                "root": root,
                "path": candidate.relative_to(self.roots[root]).as_posix(),
                "start_line": start_line,
                "end_line": min(end_line, len(lines)),
                "text": text[:64_000],
                "trust": _trust_for_root(root),
            }

        return await asyncio.wait_for(asyncio.to_thread(load), timeout=0.75)

    def _search_root_fallback(self, root_name: str, query: str, limit: int) -> list[dict[str, Any]]:
        root = self.roots[root_name]
        if not root.is_dir():
            return []
        deadline = time.monotonic() + FALLBACK_SEARCH_SECONDS
        denied_parts = _denied_parts_for_root(root_name)
        pending = [root]
        results: list[dict[str, Any]] = []
        visited_entries = 0

        while pending and len(results) < limit and visited_entries < MAX_FALLBACK_ENTRIES:
            if time.monotonic() >= deadline:
                break
            directory = pending.pop()
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    visited_entries += 1
                    if visited_entries >= MAX_FALLBACK_ENTRIES or time.monotonic() >= deadline:
                        break
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    attributes = getattr(metadata, "st_file_attributes", 0)
                    if entry.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                        continue
                    lowered_name = entry.name.lower()
                    if stat.S_ISDIR(metadata.st_mode):
                        if lowered_name not in denied_parts:
                            pending.append(Path(entry.path))
                        continue
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES or not _allowed_source_name(entry.name):
                        continue
                    candidate = Path(entry.path)
                    try:
                        relative = candidate.relative_to(root).as_posix()
                        candidate = self._safe_file(root_name, relative)
                        with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                            for line_number, line in enumerate(handle, 1):
                                if query in line:
                                    results.append({
                                        "root": root_name,
                                        "path": relative,
                                        "line": line_number,
                                        "excerpt": redact_text(line.strip())[:500],
                                        "trust": _trust_for_root(root_name),
                                    })
                                    if len(results) >= limit:
                                        break
                                if time.monotonic() >= deadline:
                                    break
                    except (OSError, ValueError):
                        continue
                    if len(results) >= limit or time.monotonic() >= deadline:
                        break
        return results

    def _search_root(self, root_name: str, query: str, limit: int) -> list[dict[str, Any]]:
        root = self.roots[root_name]
        if not root.is_dir():
            return []
        if self._rg_path is None:
            return self._search_root_fallback(root_name, query, limit)
        command = [self._rg_path, "--json", "--hidden", "-F", "--max-filesize", "512K"]
        for part in sorted(_denied_parts_for_root(root_name)):
            command.extend(("--glob", f"!**/{part}/**"))
        for name in sorted(DENIED_NAMES):
            command.extend(("--glob", f"!**/{name}"))
        extensions = ",".join(sorted(extension.removeprefix(".") for extension in ALLOWED_EXTENSIONS))
        command.extend(("--glob", f"*.{{{extensions}}}"))
        for name in sorted(ALLOWED_FILENAMES):
            command.extend(("--glob", name))
        command.extend(("--", query, "."))
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1.1,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            # Scheduled tasks do not inherit Codex's private, versioned rg path.
            # Fall back to a bounded in-process scanner instead of surfacing
            # WinError 2 or requiring a manually maintained PATH entry.
            self._rg_path = None
            return self._search_root_fallback(root_name, query, limit)
        if completed.returncode not in {0, 1}:
            return []
        results: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            relative = str(data["path"]["text"]).replace("\\", "/").removeprefix("./")
            try:
                self._safe_file(root_name, relative)
            except ValueError:
                continue
            line_number = int(data.get("line_number") or 1)
            excerpt = redact_text(str(data["lines"]["text"]).strip())[:500]
            results.append({
                "root": root_name,
                "path": relative,
                "line": line_number,
                "excerpt": excerpt,
                "trust": _trust_for_root(root_name),
            })
            if len(results) >= limit:
                break
        return results

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip()
        if len(query) < 2 or len(query) > 160 or "\x00" in query:
            raise ValueError("code query must contain 2-160 characters")
        limit = max(1, min(int(limit), 30))
        async with self._heavy_read:
            buckets: list[list[dict[str, Any]]] = []
            for root_name in self.roots:
                try:
                    found = await asyncio.wait_for(
                        asyncio.to_thread(self._search_root, root_name, query, limit),
                        timeout=1.25,
                    )
                except (asyncio.TimeoutError, subprocess.TimeoutExpired):
                    found = []
                buckets.append(found)

            # Interleave roots so a common term in MIRROW cannot starve an
            # explicitly approved reference project from the bounded result set.
            results: list[dict[str, Any]] = []
            position = 0
            while len(results) < limit:
                added = False
                for bucket in buckets:
                    if position < len(bucket):
                        results.append(bucket[position])
                        added = True
                        if len(results) >= limit:
                            break
                if not added:
                    break
                position += 1
            return results[:limit]

    async def revision(self) -> dict[str, Any]:
        def inspect_repo() -> dict[str, Any]:
            base = ["git", "-c", f"safe.directory={self.settings.repo_root.as_posix()}"]
            try:
                head = subprocess.run(
                    base + ["rev-parse", "HEAD"],
                    cwd=self.settings.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=0.75,
                    check=True,
                ).stdout.strip()
                branch = subprocess.run(
                    base + ["branch", "--show-current"],
                    cwd=self.settings.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=0.75,
                    check=True,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return {
                    "commit": None,
                    "branch": None,
                    "working_tree_dirty": None,
                    "changed_path_count": None,
                    "untracked_paths_included": False,
                    "source": "git_metadata_unavailable",
                }

            try:
                status = subprocess.run(
                    base + ["status", "--porcelain", "--untracked-files=no"],
                    cwd=self.settings.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    check=True,
                ).stdout.splitlines()
                dirty: bool | None = bool(status)
                changed_count: int | None = len(status)
                source = "live_tracked_working_tree"
            except (OSError, subprocess.SubprocessError):
                dirty = None
                changed_count = None
                source = "git_status_unavailable"
            return {
                "commit": head,
                "branch": branch or "detached",
                "working_tree_dirty": dirty,
                "changed_path_count": changed_count,
                "untracked_paths_included": False,
                "source": source,
            }

        async with self._heavy_read:
            try:
                return await asyncio.wait_for(asyncio.to_thread(inspect_repo), timeout=2.75)
            except asyncio.TimeoutError:
                return {
                    "commit": None,
                    "branch": None,
                    "working_tree_dirty": None,
                    "changed_path_count": None,
                    "untracked_paths_included": False,
                    "source": "git_metadata_timeout",
                }

    async def service_health(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._health_cache and now - self._health_cache[0] < 5:
            return self._health_cache[1]
        endpoints = {
            "backend": "http://127.0.0.1:8005/health",
        }

        async def probe(client: httpx.AsyncClient, name: str, url: str) -> tuple[str, dict[str, Any]]:
            try:
                response = await client.get(url)
                state = None
                if response.headers.get("content-type", "").startswith("application/json"):
                    payload = response.json()
                    if isinstance(payload, dict) and isinstance(payload.get("status"), (str, bool, int)):
                        state = str(payload["status"])[:40]
                return name, {"reachable": 200 <= response.status_code < 300, "http_status": response.status_code, "state": state}
            except Exception:
                return name, {"reachable": False, "http_status": None, "state": None}

        async with httpx.AsyncClient(timeout=0.5, trust_env=False, follow_redirects=False) as client:
            pairs = await asyncio.gather(*(probe(client, name, url) for name, url in endpoints.items()))
        result = dict(pairs)
        self._health_cache = (now, result)
        return result
