from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from local_secret_vault import PREFIX, protect, unprotect


def load_or_create_secret(state_dir: Path, name: str, size: int = 32) -> bytes:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{name}.dpapi"
    if path.exists():
        protected = path.read_text(encoding="utf-8")
        if not protected.startswith(PREFIX):
            raise RuntimeError(f"refusing non-DPAPI secret material: {path.name}")
        return base64.b64decode(unprotect(protected), validate=True)
    value = os.urandom(size)
    encoded = protect(base64.b64encode(value).decode("ascii"))
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return load_or_create_secret(state_dir, name, size)
    return value


class SourceCodec:
    def __init__(self, state_dir: Path):
        raw_key = load_or_create_secret(state_dir, "source-id", 32)
        self._fernet = Fernet(base64.urlsafe_b64encode(raw_key))

    def encode(self, payload: bytes) -> str:
        return self._fernet.encrypt(payload).decode("ascii")

    def decode(self, token: str) -> bytes:
        if not token or len(token) > 4096:
            raise ValueError("invalid source id")
        try:
            return self._fernet.decrypt(token.encode("ascii"), ttl=None)
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ValueError("invalid source id") from exc
