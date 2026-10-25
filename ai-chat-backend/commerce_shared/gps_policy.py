"""Address selection from the authoritative MIRROW-main-phone GPS fix."""

from __future__ import annotations

import inspect
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from .action_requests import ActionRequest, ActionRequestStore


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class LocationFix:
    lat: float | None
    lng: float | None
    captured_at: str
    accuracy_m: float | None = None
    category: str = ""
    source: str = "mirrow_main_phone_relay"

    @property
    def available(self) -> bool:
        return self.lat is not None and self.lng is not None

    def age_seconds(self, now: datetime | None = None) -> float | None:
        captured = _parse_time(self.captured_at)
        if captured is None:
            return None
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current.astimezone(timezone.utc) - captured).total_seconds())


@dataclass(frozen=True)
class AddressDecision:
    status: str
    label: str = ""
    address: Mapping[str, Any] | None = None
    reason: str = ""
    distance_m: float | None = None
    action_request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {"status": self.status, "label": self.label, "reason": self.reason}
        if self.address is not None:
            # The caller may use the full address locally.  It must not be
            # placed into a AI-facing proposal or notification.
            result["address"] = dict(self.address)
        if self.distance_m is not None:
            result["distance_m"] = round(self.distance_m, 1)
        if self.action_request_id:
            result["action_request_id"] = self.action_request_id
        return result


class GpsAddressPolicy:
    """Resolve home/work/elsewhere with freshness and accuracy gates."""

    def __init__(
        self,
        *,
        home: tuple[float, float] | None = None,
        work: tuple[float, float] | None = None,
        home_radius_m: float = 200,
        work_radius_m: float = 200,
        max_age_seconds: float = 300,
        max_accuracy_m: float = 100,
        far_candidate_m: float = 1000,
        action_store: ActionRequestStore | None = None,
    ) -> None:
        self.home = home
        self.work = work
        self.home_radius_m = max(1.0, float(home_radius_m))
        self.work_radius_m = max(1.0, float(work_radius_m))
        self.max_age_seconds = max(1.0, float(max_age_seconds))
        self.max_accuracy_m = max(1.0, float(max_accuracy_m))
        self.far_candidate_m = max(50.0, float(far_candidate_m))
        self.action_store = action_store

    @staticmethod
    def distance_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
        radius = 6371000.0
        phi1, phi2 = math.radians(float(a_lat)), math.radians(float(b_lat))
        dphi, dlambda = math.radians(float(b_lat) - float(a_lat)), math.radians(float(b_lng) - float(a_lng))
        hav = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return radius * 2 * math.atan2(math.sqrt(max(0.0, hav)), math.sqrt(max(0.0, 1 - hav)))

    def classify(self, fix: LocationFix, *, now: datetime | None = None) -> str:
        if not fix.available:
            return "unknown"
        age = fix.age_seconds(now)
        if age is None or age > self.max_age_seconds:
            return "unknown"
        if fix.accuracy_m is None or float(fix.accuracy_m) > self.max_accuracy_m:
            return "unknown"
        # Match the existing GpsSource precedence: work wins if anchors
        # overlap, then home.
        if self.work is not None and self.distance_m(fix.lat, fix.lng, *self.work) <= self.work_radius_m:
            return "work"
        if self.home is not None and self.distance_m(fix.lat, fix.lng, *self.home) <= self.home_radius_m:
            return "home"
        return "elsewhere"

    def select_saved_address(self, fix: LocationFix, saved_addresses: list[Mapping[str, Any]], *, now: datetime | None = None) -> AddressDecision:
        category = self.classify(fix, now=now)
        if category not in {"home", "work"}:
            return AddressDecision("needs_confirmation", reason="gps_elsewhere_or_unavailable")
        wanted = category.lower()
        candidates = [dict(item) for item in saved_addresses if str(item.get("label") or item.get("tag") or item.get("type") or "").lower() == wanted]
        if not candidates:
            return AddressDecision("needs_confirmation", label=wanted, reason="matching_saved_address_missing")
        selected = candidates[0]
        distance = None
        # If the saved-address parser supplied coordinates, validate the
        # chosen candidate against the current GPS before using it.
        try:
            a_lat = selected.get("lat", selected.get("latitude"))
            a_lng = selected.get("lng", selected.get("longitude"))
            if a_lat is not None and a_lng is not None:
                distance = self.distance_m(fix.lat, fix.lng, float(a_lat), float(a_lng))
                if distance > self.far_candidate_m:
                    return AddressDecision("needs_confirmation", label=wanted, address=selected, distance_m=distance, reason="saved_address_far_from_gps")
        except (TypeError, ValueError):
            distance = None
        return AddressDecision("selected", label=wanted, address=selected, distance_m=distance, reason="fresh_main_phone_gps_match")

    def create_elsewhere_request(self, *, task_id: str, saved_contact_hint: str = "", request_id: str | None = None) -> ActionRequest:
        if self.action_store is None:
            raise RuntimeError("action_request_store_required")
        # Only non-sensitive presentation hints are persisted.  The actual
        # address entered by the user is stored as private action payload.
        payload = {"task_id": str(task_id or "")[:100], "contact_hint": str(saved_contact_hint or "")[:80]}
        return self.action_store.create(
            "delivery_address",
            fields=("place", "detail", "confirm", "cancel"),
            payload=payload,
            request_id=request_id,
        )

    async def force_refresh(self, relay: Callable[..., Any]) -> LocationFix | None:
        """Force a fresh fix from the MIRROW-main-phone relay.

        The relay signature is the existing ``relay(request_id, tool, params)``
        contract.  No server/USB shopping-phone GPS is consulted here.
        """
        if relay is None:
            return None
        try:
            result = relay(str(uuid.uuid4()), "mobile_location", {"force": True})
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping) or not result.get("success"):
                return None
            data = result.get("data") or {}
            if isinstance(data, str):
                import json
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            lat, lng = data.get("lat"), data.get("lng")
            if lat is None or lng is None:
                return None
            captured = data.get("captured_at") or data.get("timestamp") or datetime.now(timezone.utc).isoformat()
            accuracy = data.get("accuracy_m", data.get("accuracy"))
            return LocationFix(float(lat), float(lng), str(captured), float(accuracy) if accuracy is not None else None, source="mirrow_main_phone_relay")
        except (TypeError, ValueError, KeyError):
            return None
        except Exception:
            return None


__all__ = ["AddressDecision", "GpsAddressPolicy", "LocationFix"]
