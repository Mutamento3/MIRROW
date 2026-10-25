"""Source-bound receipts for optional generated body prose (not body facts)."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def digest_text(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def authority_signature(paths):
    values = []
    for path in sorted(set(map(Path, paths))):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            digest = None
        values.append((str(path), digest))
    return digest_text(json.dumps(values, ensure_ascii=False))


def receipt_path(cache):
    return cache.with_name(".brain_architecture.source.json")


def is_current(cache, content, signature):
    try:
        receipt = json.loads(receipt_path(cache).read_text("utf8"))
        return (isinstance(receipt, dict) and receipt.get("version") == 1
                and receipt.get("authority") == signature
                and receipt.get("content") == digest_text(content))
    except (OSError, ValueError, TypeError):
        return False


def write_receipt(cache, content, signature):
    # A crash between narrative and receipt leaves the narrative stale; it
    # cannot authorize old/partial prose as a current body fact.
    path = receipt_path(cache)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent,
                                         prefix=".brain_receipt.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "authority": signature, "content": digest_text(content)}, stream)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
