"""Pluggable system credential storage for delivery contact details.

There is deliberately no JSON/SQLite plaintext fallback.  Production can use
the OS keyring (or a platform provider supplied by the host); tests inject the
in-memory provider.  The first read from Meituan is a user-confirmed action,
and masked phone values are never guessed or bypassed.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .action_requests import ActionRequest, ActionRequestStore


class SystemCredentialProvider(Protocol):
    """Minimal replaceable OS credential-store contract."""

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class CredentialProviderUnavailable(RuntimeError):
    """Raised when the host has no usable system secret store."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DpapiCredentialProvider:
    """Windows-current-user DPAPI backed credential store.

    DPAPI performs the encryption; the JSON file contains only base64-encoded
    DPAPI ciphertext.  Base64 is an encoding here, never the security layer.
    The key is scoped to the current Windows user and the provider purpose, so
    another user or a different MIRROW credential service cannot decrypt it.
    """

    _VERSION = 1
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    _MAX_KEY_LENGTH = 200

    def __init__(self, path: str | Path | None = None, *, service: str = "mirrow.commerce") -> None:
        if os.name != "nt":
            raise CredentialProviderUnavailable("dpapi_requires_windows")
        self.path = Path(path) if path else Path(__file__).resolve().parents[1] / "data" / "credentials" / "dpapi.json"
        self.service = str(service or "mirrow.commerce")[:120]
        try:
            self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise CredentialProviderUnavailable("dpapi_unavailable") from exc
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_int
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @classmethod
    def is_available(cls) -> bool:
        if os.name != "nt":
            return False
        try:
            ctypes.WinDLL("crypt32", use_last_error=True)
            ctypes.WinDLL("kernel32", use_last_error=True)
            return True
        except (AttributeError, OSError):
            return False

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        if not value:
            raise ValueError("empty_dpapi_blob")
        buffer = ctypes.create_string_buffer(value)
        blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    def _entropy(self) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        return self._blob(self.service.encode("utf-8"))

    @staticmethod
    def _win_error(operation: str) -> CredentialProviderUnavailable:
        error = ctypes.get_last_error()
        return CredentialProviderUnavailable(f"{operation}_failed:{error}")

    def _protect(self, value: str) -> bytes:
        source, source_buffer = self._blob(str(value).encode("utf-8"))
        entropy, entropy_buffer = self._entropy()
        result = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source), "MIRROW commerce credential", ctypes.byref(entropy),
            None, None, self._CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        )
        # Keep input buffers alive until CryptProtectData returns.
        del source_buffer, entropy_buffer
        if not ok or not result.pbData:
            raise self._win_error("dpapi_protect")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self._kernel32.LocalFree(result.pbData)

    def _unprotect(self, value: bytes) -> str:
        source, source_buffer = self._blob(value)
        entropy, entropy_buffer = self._entropy()
        result = _DataBlob()
        description = ctypes.c_wchar_p()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
            None, None, self._CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        )
        del source_buffer, entropy_buffer
        if not ok or not result.pbData:
            raise self._win_error("dpapi_unprotect")
        try:
            return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialProviderUnavailable("dpapi_invalid_utf8") from exc
        finally:
            self._kernel32.LocalFree(result.pbData)

    @classmethod
    def _key(cls, key: str) -> str:
        value = str(key or "").strip()
        if not value or len(value) > cls._MAX_KEY_LENGTH:
            raise ValueError("credential_key_invalid")
        return value

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries", {}) if isinstance(raw, dict) else {}
            if not isinstance(entries, dict):
                raise ValueError("entries_not_object")
            return {str(key): str(value) for key, value in entries.items() if isinstance(value, str)}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CredentialProviderUnavailable("credential_store_corrupt") from exc

    def _write(self, entries: Mapping[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = json.dumps({"version": self._VERSION, "entries": dict(entries)}, ensure_ascii=True, sort_keys=True).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def get(self, key: str) -> str | None:
        encoded = self._read().get(self._key(key))
        if not encoded:
            return None
        try:
            encrypted = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialProviderUnavailable("credential_ciphertext_invalid") from exc
        return self._unprotect(encrypted)

    def set(self, key: str, value: str) -> None:
        entries = self._read()
        entries[self._key(key)] = base64.b64encode(self._protect(str(value))).decode("ascii")
        self._write(entries)

    def delete(self, key: str) -> None:
        entries = self._read()
        entries.pop(self._key(key), None)
        self._write(entries)


class KeyringCredentialProvider:
    """Use Python keyring backed by the platform credential store."""

    def __init__(self, service: str = "mirrow.commerce") -> None:
        self.service = str(service or "mirrow.commerce")

    def _module(self):
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise RuntimeError("system_credential_provider_unavailable") from exc
        return keyring

    def get(self, key: str) -> str | None:
        return self._module().get_password(self.service, str(key))

    def set(self, key: str, value: str) -> None:
        self._module().set_password(self.service, str(key), str(value))

    def delete(self, key: str) -> None:
        self._module().delete_password(self.service, str(key))


def build_production_credential_provider(*, path: str | Path | None = None) -> SystemCredentialProvider:
    """Select a real OS-backed provider without a plaintext fallback.

    Windows deployments use DPAPI directly.  Other platforms may use the
    configured Python keyring backend; if it is unavailable, the caller gets a
    provider error and must ask for the value again rather than persisting it
    in JSON/SQLite.
    """
    if os.name == "nt":
        return DpapiCredentialProvider(path=path)
    return KeyringCredentialProvider()


class MemoryCredentialProvider:
    """Explicit test double; never used as a production fallback."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(str(key))

    def set(self, key: str, value: str) -> None:
        self.values[str(key)] = str(value)

    def delete(self, key: str) -> None:
        self.values.pop(str(key), None)


def _masked(value: str) -> bool:
    value = str(value or "")
    return "*" in value or "•" in value or "×" in value or "x" in value.lower()


def mask_phone(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if _masked(value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        return digits[:3] + "****" + digits[-4:]
    return "****"


@dataclass(frozen=True)
class ContactValue:
    name: str
    phone: str

    def masked(self) -> dict[str, str]:
        return {"name": self.name[:1] + "*" if self.name else "", "phone": mask_phone(self.phone)}


class ContactCache:
    """User-confirmed Meituan contact cache backed by a credential provider."""

    KEY = "meituan.delivery_contact.v1"

    def __init__(self, provider: SystemCredentialProvider):
        self.provider = provider

    def get(self) -> ContactValue | None:
        raw = self.provider.get(self.KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            name, phone = str(data.get("name") or ""), str(data.get("phone") or "")
            if not name or not phone or _masked(phone):
                return None
            return ContactValue(name, phone)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set(self, *, name: str, phone: str, user_confirmed: bool = False) -> ContactValue:
        if not user_confirmed:
            raise PermissionError("contact_confirmation_required")
        name, phone = str(name or "").strip(), str(phone or "").strip()
        if not name:
            raise ValueError("contact_name_required")
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 6 or _masked(phone):
            raise ValueError("complete_unmasked_phone_required")
        contact = ContactValue(name[:80], phone[:40])
        self.provider.set(self.KEY, json.dumps({"name": contact.name, "phone": contact.phone}, ensure_ascii=False))
        return contact

    def clear(self) -> None:
        self.provider.delete(self.KEY)

    def masked(self) -> dict[str, str] | None:
        contact = self.get()
        return contact.masked() if contact else None


class MeituanContactWorkflow:
    """First-read confirmation workflow for address-management UI extraction."""

    def __init__(self, cache: ContactCache, action_store: ActionRequestStore):
        self.cache = cache
        self.action_store = action_store

    def cached(self) -> ContactValue | None:
        return self.cache.get()

    def inspect_readonly_addresses(self, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Return masked, non-sensitive read results from the app UI.

        ``rows`` is expected to come from a read-only page dump.  No attempt is
        made to recover a masked phone number.  Values are keyed by label only
        in this result so it is safe to display as a UI checkpoint.
        """
        labels = []
        contacts: list[dict[str, str]] = []
        for row in rows:
            label = str(row.get("label") or row.get("tag") or "")[:30]
            if label:
                labels.append(label)
            name = str(row.get("name") or "")
            phone = str(row.get("phone") or "")
            contacts.append({"label": label, "name": name[:1] + "*" if name else "", "phone": mask_phone(phone)})
        return {"labels": labels, "contacts": contacts, "source": "meituan_address_management_readonly"}

    def begin_first_read(self, rows: list[Mapping[str, Any]]) -> ActionRequest | None:
        if self.cache.get() is not None:
            return None
        display = self.inspect_readonly_addresses(rows)
        # Store only opaque metadata; the original rows stay in the domain
        # adapter's transient memory until the user confirms edited fields.
        recognized_rows = [
            f"{item.get('label', '')}: {item.get('name', '')} {item.get('phone', '')}".strip()
            for item in display.get("contacts", [])
            if isinstance(item, Mapping)
        ]
        return self.action_store.create(
            "meituan_contact_confirmation",
            fields=("name", "phone", "confirm", "cancel"),
            payload={"recognized_rows": recognized_rows, "warning": "建议使用虚拟姓名，尽量不要使用真实姓名"},
        )

    def confirm(self, action_request_id: str, *, name: str, phone: str, confirmed: bool) -> ContactValue:
        if not confirmed:
            raise PermissionError("contact_confirmation_required")
        contact = self.cache.set(name=name, phone=phone, user_confirmed=True)
        self.action_store.resolve(action_request_id, {"confirmed": True, "fields": ["name", "phone"]})
        return contact

    def cancel(self, action_request_id: str) -> dict[str, Any]:
        return self.action_store.cancel(action_request_id, reason="contact_not_confirmed")


__all__ = [
    "ContactCache", "ContactValue", "CredentialProviderUnavailable",
    "DpapiCredentialProvider", "KeyringCredentialProvider",
    "MemoryCredentialProvider", "MeituanContactWorkflow", "SystemCredentialProvider",
    "build_production_credential_provider", "mask_phone",
]
