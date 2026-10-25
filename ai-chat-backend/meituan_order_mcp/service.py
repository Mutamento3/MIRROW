"""Meituan domain service with strict favorite-store and payment gates."""

from __future__ import annotations

import inspect
import os
import asyncio
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from commerce_shared.analysis import build_candidate_options, plan_meituan_coupons
from commerce_shared.contracts import ShoppingCandidate, ShoppingProposal, ShoppingRequest
from commerce_shared.action_requests import ActionRequestStore
from commerce_shared.gps_policy import AddressDecision, GpsAddressPolicy, LocationFix
from commerce_shared.limits import DEFAULT_LIMITS, LimitLedger, money_cents
from commerce_shared.preferences import ShoppingDecisionLedger, queue_pending_self_book_candidate, should_queue_preference
from commerce_shared.state import CommerceState, CommerceStateStore, InvalidStateTransition, RequestConflict


def _default_db() -> Path:
    root = Path(os.getenv("MIRROW_COMMERCE_DATA_DIR", "data"))
    return root / "meituan-order-mcp" / "meituan.db"


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_request(value: ShoppingRequest | Mapping[str, Any]) -> ShoppingRequest:
    if isinstance(value, ShoppingRequest):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("shopping_request_mapping_required")
    return ShoppingRequest(
        merchant="meituan", brief=value.get("brief", ""), max_payable=value.get("max_payable"),
        request_id=value.get("request_id") or __import__("uuid").uuid4().hex,
        fulfillment_mode=value.get("fulfillment_mode", "delivery"), address_label=value.get("address_label", "default"),
        constraints=value.get("constraints") or {},
    )


def _favorite(raw: Mapping[str, Any]) -> bool:
    value = raw.get("is_favorite", raw.get("favorite", raw.get("from_favorites")))
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是", "收藏"}
    return bool(value)


class MeituanOrderService:
    """AI-facing delivery request/proposal service; checkout remains disabled."""

    merchant = "meituan"
    execution_capability = "disabled"

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        candidate_provider: Callable[[dict[str, Any]], Any] | None = None,
        runtime_store: Any = None,
        executor: Callable[..., Any] | None = None,
        gps_policy: GpsAddressPolicy | None = None,
        relay_location_reader: Callable[..., Any] | None = None,
        saved_addresses: Iterable[Mapping[str, Any]] | None = None,
        reservation_ttl_seconds: int | float = 900,
    ) -> None:
        self.db_path = Path(db_path or _default_db())
        self.state = CommerceStateStore(self.db_path)
        self.limits = LimitLedger(self.db_path)
        self.decisions = ShoppingDecisionLedger(self.db_path)
        self.candidate_provider = candidate_provider
        self.runtime_store = runtime_store
        self.executor = executor
        self.action_store = ActionRequestStore(self.db_path)
        # Production must never silently fall back to the shopping phone's
        # location.  With no injected main-phone relay this policy remains
        # unavailable and confirmation waits for an explicit address action.
        self.gps_policy = gps_policy or GpsAddressPolicy(action_store=self.action_store)
        self.relay_location_reader = relay_location_reader
        self.saved_addresses = [dict(item) for item in (saved_addresses or ()) if isinstance(item, Mapping)]
        self.reservation_ttl_seconds = max(1, min(86400, int(float(reservation_ttl_seconds))))

    def submit_request(self, request: ShoppingRequest | Mapping[str, Any]) -> dict[str, Any]:
        request = _as_request(request)
        if request.max_payable is None:
            return {"success": False, "status": "invalid_request", "error": "max_payable_required"}
        if request.fulfillment_mode != "delivery":
            return {"success": False, "status": "unsupported", "error": "meituan_v1_delivery_only"}
        if money_cents(request.max_payable) > self.limits.get_config(self.merchant).per_order_cents:
            return {"success": False, "status": "limit_rejected", "error": "max_payable_exceeds_configured_cap"}
        try:
            row = self.state.create_request(request)
        except RequestConflict as exc:
            return {"success": False, "status": "conflict", "error": str(exc), "request_id": request.request_id}
        if row["state"] == CommerceState.REQUESTED.value:
            row = self.state.transition(request.request_id, CommerceState.RESEARCHING.value, reason="structured_brief_accepted")
        return self._public_request(row)

    async def preview_device(self, *, adapter=None) -> dict[str, Any]:
        """Observe the real browsing entrance without creating a purchase task."""
        if adapter is None:
            from .device_preview import MeituanDevicePreview
            adapter = MeituanDevicePreview()
        result = await adapter.inspect()
        return dict(result, execution_capability=self.execution_capability,
                    proposal_created=False, success=result.get('status') == 'observed')

    def get_proposal(self, request_id: str, candidates: Iterable[Mapping[str, Any] | ShoppingCandidate] | None = None) -> dict[str, Any]:
        row = self.state.get_request(request_id)
        if row is None:
            return {"success": False, "status": "not_found", "error": "request_not_found"}
        if row["merchant"] != self.merchant:
            return {"success": False, "status": "invalid_request", "error": "merchant_mismatch"}
        if candidates is None and self.candidate_provider is not None:
            candidates = self.candidate_provider(self._public_request(row))
            if inspect.isawaitable(candidates):
                return {"success": False, "status": "awaiting_device_adapter", "error": "async_adapter_requires_host"}
        if candidates is None:
            return {"success": False, "status": "awaiting_device_adapter", "error": "candidate_adapter_not_configured"}
        request_cap = money_cents(row["max_payable"])
        configured_cap = self.limits.get_config(self.merchant).per_order_cents
        accepted: list[Mapping[str, Any] | ShoppingCandidate] = []
        rejected: list[str] = []
        for raw in candidates:
            if isinstance(raw, ShoppingCandidate):
                try:
                    price = money_cents(raw.final_payable)
                except ValueError:
                    rejected.append("candidate_price_invalid")
                    continue
                if price > request_cap or price > configured_cap:
                    rejected.append("candidate_over_limit")
                    continue
                accepted.append(raw)
                continue
            if not isinstance(raw, Mapping):
                rejected.append("candidate_not_mapping")
                continue
            # A missing favorite flag is denied rather than interpreted as
            # favorite; this prevents a future adapter from broadening scope.
            if not _favorite(raw):
                rejected.append("not_from_favorites")
                continue
            if not _text(raw.get("store_name", raw.get("shop_name", "")), 120) or not _text(raw.get("branch_address", raw.get("store_address", "")), 200):
                rejected.append("store_or_branch_identity_missing")
                continue
            try:
                plan = plan_meituan_coupons(raw, include_optional_add_on=True)
                enriched = dict(raw)
                enriched["final_payable"] = plan.final_total
                enriched["reason"] = _text(raw.get("reason") or "收藏店铺门店已核对；结算页优惠后的实付", 700)
                enriched["coupon_breakdown"] = plan.to_dict()
                accepted.append(enriched)
            except (TypeError, ValueError):
                rejected.append("candidate_total_invalid")
                continue
            if money_cents(plan.final_total) > request_cap or money_cents(plan.final_total) > configured_cap:
                accepted.pop()
                rejected.append("candidate_over_limit")
        options, limitations = build_candidate_options(accepted, merchant=self.merchant)
        limitations = tuple(list(limitations) + ([f"过滤 {len(rejected)} 个不符合收藏店铺/限额的候选"] if rejected else []))
        if not options:
            if row["state"] == CommerceState.RESEARCHING.value:
                self.state.transition(request_id, CommerceState.NEEDS_USER.value, reason="no_eligible_favorite_store")
            return {"success": False, "status": "no_eligible_candidates", "limitations": list(limitations)}
        proposal = ShoppingProposal(request_id=request_id, merchant=self.merchant, candidates=options, limitations=limitations)
        stored = self.state.save_proposal(request_id, proposal)
        return {"success": True, "status": "awaiting_confirmation", "proposal": stored.get("proposal")}

    def confirm_candidate(
        self, request_id: str, candidate_id: str, *, repeated_principle: bool = False,
        explicit_persistent: bool = False, preference_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.state.get_request(request_id)
        if current is None or current.get("merchant") != self.merchant:
            return {"success": False, "status": "invalid_request", "error": "request_not_found"}
        if current.get("state") not in {CommerceState.AWAITING_CONFIRMATION.value, CommerceState.PROPOSED.value, CommerceState.CONFIRMED.value}:
            return {"success": False, "status": "invalid_request", "error": "candidate_selection_not_allowed"}
        if current.get("state") == CommerceState.CONFIRMED.value and current.get("selected_candidate_id") and str(current.get("selected_candidate_id")) != str(candidate_id):
            # Check immutable state before changing the quota ledger.
            return {"success": False, "status": "invalid_request", "error": "candidate_immutable_after_confirmation"}
        proposal = current.get("proposal") or {}
        selected = next((item for item in proposal.get("candidates", []) if str(item.get("candidate_id")) == str(candidate_id)), {})
        if not selected:
            return {"success": False, "status": "invalid_request", "error": "candidate_not_found"}
        prior_reservation = self.limits.reservation(request_id)
        if current.get("state") == CommerceState.CONFIRMED.value and prior_reservation and prior_reservation.get("status") == "expired":
            try:
                self.state.transition(request_id, CommerceState.AWAITING_CONFIRMATION.value, reason="limit_reservation_expired")
                current = self.state.get_request(request_id) or current
            except InvalidStateTransition:
                return {"success": False, "status": "limit_reservation_expired", "error": "confirmation_required_again"}
        location = self._sync_address_decision(request_id, current.get("address_label", "default"))
        if location.status != "selected":
            result = {"success": False, "status": "awaiting_location", "error": location.reason, "request_id": request_id}
            if location.action_request_id:
                result["action_request_id"] = location.action_request_id
            return result
        try:
            amount = money_cents(selected.get("final_payable"))
        except (TypeError, ValueError):
            return {"success": False, "status": "invalid_request", "error": "candidate_price_invalid"}
        reservation = self.limits.reserve(self.merchant, request_id, amount / 100, reservation_ttl_seconds=self.reservation_ttl_seconds)
        newly_reserved = reservation.reason in {"ok", "re_reserved"}
        if not reservation.allowed:
            if reservation.reason == "order_id_already_bound":
                reservation = self.limits.adjust(self.merchant, request_id, amount / 100, reservation_ttl_seconds=self.reservation_ttl_seconds)
            if not reservation.allowed:
                return {"success": False, "status": "limit_rejected", "error": reservation.reason, "limits": reservation.to_dict()}
        try:
            row = self.state.select_candidate(request_id, candidate_id)
        except (KeyError, ValueError, InvalidStateTransition) as exc:
            if newly_reserved:
                self.limits.release(request_id)
            elif prior_reservation and prior_reservation.get("status") == "reserved" and float(prior_reservation.get("amount", 0)) != amount / 100:
                self.limits.adjust(self.merchant, request_id, prior_reservation["amount"], reservation_ttl_seconds=self.reservation_ttl_seconds)
            return {"success": False, "status": "invalid_request", "error": str(exc)}
        decision = self.decisions.append(
            task_id=request_id, merchant=self.merchant, candidate_id=candidate_id,
            choice=selected.get("kind", "candidate"), reason=selected.get("reason", "AI confirmed candidate"), evidence=selected.get("evidence", []),
        )
        result: dict[str, Any] = {"success": True, "status": "confirmed", "request_id": request_id, "candidate_id": candidate_id, "decision_id": decision.decision_id, "address_label": location.label, "limit": reservation.to_dict()}
        if should_queue_preference(repeated_principle=repeated_principle, explicit_persistent=explicit_persistent):
            ctx = dict(preference_context or {})
            if self.runtime_store is not None and all(ctx.get(key) for key in ("run_id", "activity_id", "node_id", "wander_decision_id")):
                result["pending_self_book_candidate_ids"] = queue_pending_self_book_candidate(
                    self.runtime_store, run_id=ctx["run_id"], activity_id=ctx["activity_id"], node_id=ctx["node_id"], decision_id=ctx["wander_decision_id"],
                    statement=_text(ctx.get("statement") or "购物选择体现了一个待确认的持久偏好", 1000), category="shopping_preference",
                    keywords=tuple(ctx.get("keywords") or ()), shopping_task_ids=(request_id,),
                )
            else:
                result["preference_status"] = "pending_wander_identity_context"
        return result

    def get_status(self, request_id: str) -> dict[str, Any]:
        row = self.state.get_request(request_id)
        if row is None:
            return {"success": False, "status": "not_found", "error": "request_not_found"}
        row = self._refresh_reservation_state(row)
        return {"success": True, "status": row.get("state"), "request": self._public_request(row), "selected_candidate_id": row.get("selected_candidate_id") or "", "execution_capability": self.execution_capability}

    def _refresh_reservation_state(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if row.get("state") == CommerceState.CONFIRMED.value:
            reservation = self.limits.reservation(str(row.get("request_id") or ""))
            if reservation and reservation.get("status") == "expired":
                try:
                    return self.state.transition(str(row.get("request_id")), CommerceState.AWAITING_CONFIRMATION.value, reason="limit_reservation_expired")
                except InvalidStateTransition:
                    pass
        return dict(row)

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        row = self.state.get_request(request_id)
        if row is None:
            return {"success": False, "status": "not_found", "error": "request_not_found"}
        if row.get("state") == CommerceState.CANCELLED.value:
            self.limits.release(request_id)
            return {"success": True, "status": "cancelled", "request_id": request_id}
        try:
            row = self.state.transition(request_id, CommerceState.CANCELLED.value, reason="cancelled_by_k")
        except InvalidStateTransition:
            return {"success": False, "status": "cannot_cancel", "error": "request_already_terminal"}
        self.limits.release(request_id)
        return {"success": True, "status": row.get("state"), "request_id": request_id}

    def _new_address_action(self, request_id: str) -> str:
        stable_id = f"delivery-address-{str(request_id)[:80]}"
        request_identity: str | None = stable_id
        if hasattr(self.gps_policy, "action_store") and self.gps_policy.action_store is not None:
            existing = self.gps_policy.action_store.get(stable_id)
            if existing and existing.get("status") in {"expired", "resolved", "cancelled"}:
                # A terminal/expired form must not be replayed under the same
                # identity; generate a fresh action request on rebuild.
                request_identity = None
        action = self.gps_policy.create_elsewhere_request(task_id=request_id, request_id=request_identity) if hasattr(self.gps_policy, "create_elsewhere_request") else None
        return action.request_id if action else ""

    def _sync_address_decision(self, request_id: str, requested_label: str) -> AddressDecision:
        """Use only a synchronous main-phone relay in the sync AI tool path."""
        if self.relay_location_reader is None:
            return AddressDecision("needs_confirmation", reason="main_phone_gps_relay_unavailable", action_request_id=self._new_address_action(request_id))
        try:
            result = self.relay_location_reader(str(request_id), "mobile_location", {"force": True})
            if inspect.isawaitable(result):
                return AddressDecision("needs_confirmation", reason="main_phone_gps_refresh_async", action_request_id=self._new_address_action(request_id))
            if isinstance(result, LocationFix):
                fix = result
            elif isinstance(result, Mapping) and result.get("success"):
                data = result.get("data") or result
                if not isinstance(data, Mapping) or data.get("source") != "mirrow_main_phone_relay":
                    # Mapping fixes must carry an explicit authoritative
                    # source; absence is not interpreted as trusted.
                    data = None
                if data is None:
                    fix = None
                else:
                    fix = LocationFix(float(data.get("lat")), float(data.get("lng")), str(data.get("captured_at") or data.get("timestamp") or ""), float(data.get("accuracy_m", data.get("accuracy"))) if data.get("accuracy_m", data.get("accuracy")) is not None else None, source=str(data.get("source")))
            else:
                fix = None
            if fix is not None and fix.source != "mirrow_main_phone_relay":
                # The dedicated shopping phone is never an address authority.
                fix = None
            if fix is None:
                return AddressDecision("needs_confirmation", reason="main_phone_gps_unavailable", action_request_id=self._new_address_action(request_id))
            decision = self.gps_policy.select_saved_address(fix, self.saved_addresses)
            if decision.status == "selected" and requested_label not in {"", "default", decision.label}:
                return AddressDecision("needs_confirmation", label=requested_label, reason="requested_address_does_not_match_gps", action_request_id=self._new_address_action(request_id))
            if decision.status != "selected":
                return AddressDecision(decision.status, decision.label, decision.address, decision.reason, decision.distance_m, self._new_address_action(request_id))
            return decision
        except (TypeError, ValueError, KeyError):
            return AddressDecision("needs_confirmation", reason="main_phone_gps_invalid", action_request_id=self._new_address_action(request_id))

    async def prepare_order(self, request_id: str) -> dict[str, Any]:
        """Force-refresh the authoritative main-phone GPS before any future submit."""
        row = self.state.get_request(request_id)
        if row is None:
            return {"success": False, "status": "not_found", "error": "request_not_found"}
        fix = await self.gps_policy.force_refresh(self.relay_location_reader)
        if fix is None:
            return {"success": False, "status": "awaiting_location", "error": "main_phone_gps_unavailable", "action_request_id": self._new_address_action(request_id)}
        decision = self.gps_policy.select_saved_address(fix, self.saved_addresses)
        if decision.status != "selected":
            return {"success": False, "status": "awaiting_location", **decision.to_dict(), "action_request_id": decision.action_request_id or self._new_address_action(request_id)}
        return {"success": True, "status": "ready", "request_id": request_id, "address_label": decision.label}

    def prepare_submission(self, request_id: str, *, submit_token: str, binding: Mapping[str, Any]) -> dict[str, Any]:
        if self.execution_capability == 'disabled':
            # A disabled adapter has not attempted submission and must not
            # claim the durable submit token or advance the order lifecycle.
            return {'success': False, 'status': 'awaiting_device_adapter', 'error': 'unsupported',
                    'execution_capability': self.execution_capability, 'request_id': request_id,
                    'newly_claimed': False}
        row = self.state.get_request(request_id)
        if row is not None:
            row = self._refresh_reservation_state(row)
            if row.get("state") != CommerceState.CONFIRMED.value:
                return {"success": False, "status": "awaiting_confirmation", "error": "limit_reservation_expired" if row.get("selected_candidate_id") else "submit_requires_confirmed_candidate"}
        try:
            claim = self.state.claim_submit(request_id, submit_token, binding=binding)
        except (KeyError, ValueError, InvalidStateTransition) as exc:
            return {"success": False, "status": "invalid_submission", "error": str(exc)}
        return {"success": False, "status": "awaiting_device_adapter", "error": "unsupported", "execution_capability": self.execution_capability, "request_id": request_id, "submit_state": claim.state, "newly_claimed": claim.newly_claimed}

    def reconcile_submission(self, request_id: str, *, found: bool, order_ref: str = "") -> dict[str, Any]:
        row = self.state.get_request(request_id)
        if row is None:
            return {"success": False, "status": "not_found", "error": "request_not_found"}
        if found and row.get("submit_token"):
            updated = self.state.mark_submitted(request_id, row["submit_token"], order_ref=order_ref)
            self.limits.finalize(request_id)
        else:
            updated = self.state.reconcile_result(request_id, found=False, reason="order_not_found_after_interruption") if row.get("state") == CommerceState.RECONCILE_REQUIRED.value else row
            self.limits.release(request_id)
        return {"success": bool(found), "status": updated.get("state"), "request_id": request_id}

    def submit_order(self, request_id: str, **_kwargs: Any) -> dict[str, Any]:
        row = self.state.get_request(request_id)
        if row is not None:
            row = self._refresh_reservation_state(row)
        if row is not None and row.get("state") == CommerceState.CONFIRMED.value:
            reservation = self.limits.reservation(request_id)
            if not reservation or reservation.get("status") != "reserved":
                return {"success": False, "status": "limit_rejected", "error": "reservation_missing_or_limit_changed", "request_id": _text(request_id, 120)}
        return {"success": False, "status": "awaiting_device_adapter", "error": "unsupported", "execution_capability": self.execution_capability, "request_id": _text(request_id, 120)}

    @staticmethod
    def _public_request(row: Mapping[str, Any]) -> dict[str, Any]:
        return {"success": True, "request_id": _text(row.get("request_id"), 120), "merchant": "meituan", "brief": _text(row.get("brief"), 1000), "max_payable": row.get("max_payable"), "fulfillment_mode": row.get("fulfillment_mode"), "address_label": _text(row.get("address_label"), 30), "constraints": dict(row.get("constraints") or {}), "state": row.get("state")}


__all__ = ["MeituanOrderService"]
