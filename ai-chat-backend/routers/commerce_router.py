"""Thin MIRROW API adapter for the independent commerce MCPs.

This router owns no product/store reasoning.  It forwards structured briefs
and compact IDs to a domain service.  Address/contact form values are kept in
process memory until a future domain adapter consumes them; they are never
written to ordinary SQLite, notifications, logs or chat context.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from commerce_shared.action_requests import ActionRequestStore, ActionRequestTerminal
from commerce_shared.credentials import ContactCache, MeituanContactWorkflow, build_production_credential_provider
from commerce_shared.limits import DEFAULT_LIMITS, LimitConfig
from commerce_shared.gps_policy import GpsAddressPolicy
from commerce_tool_adapter import get_commerce_services


router = APIRouter(prefix="/api/commerce", tags=["commerce"])
_action_store: ActionRequestStore | None = None
_contact_workflow: MeituanContactWorkflow | None = None
_volatile_action_values: dict[str, tuple[dict[str, str], float]] = {}


def _store() -> ActionRequestStore:
    global _action_store
    if _action_store is None:
        path = os.path.join(os.getenv("MIRROW_COMMERCE_DATA_DIR", "data"), "commerce.db")
        _action_store = ActionRequestStore(path)
    return _action_store


def _workflow() -> MeituanContactWorkflow:
    global _contact_workflow
    if _contact_workflow is None:
        # Production must use an OS-backed provider.  Windows uses DPAPI;
        # other hosts use their configured keyring.  There is no in-memory or
        # base64 fallback for a persistent contact.
        credential_path = os.getenv("MIRROW_COMMERCE_CREDENTIALS_PATH") or None
        _contact_workflow = MeituanContactWorkflow(
            ContactCache(build_production_credential_provider(path=credential_path)), _store()
        )
    return _contact_workflow


def _service(merchant: str):
    merchant = str(merchant or "").lower()
    service = get_commerce_services().get(merchant)
    if service is None:
        raise HTTPException(status_code=400, detail="merchant_must_be_meituan")
    return service


class ShoppingSubmitBody(BaseModel):
    brief: str = Field(min_length=1, max_length=1000)
    max_payable: float | str
    address_label: Literal["default", "home", "work"] = "default"
    constraints: dict[str, Any] = Field(default_factory=dict)


class ProposalBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    # Candidate rows are supplied by a reviewed domain adapter in local
    # development.  AI never sees or constructs raw page material here.
    candidates: list[dict[str, Any]] | None = None


class CandidateBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    candidate_id: str = Field(min_length=1, max_length=160)
    repeated_principle: bool = False
    explicit_persistent: bool = False


class RequestIdBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class ActionResolutionBody(BaseModel):
    confirmed: bool = False
    place: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=500)
    name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=40)


class ContactUpdateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=6, max_length=40)
    confirmed: bool = False


class LimitConfigBody(BaseModel):
    per_order: float = Field(gt=0, le=100000)
    daily_total: float = Field(gt=0, le=100000)
    daily_orders: int | None = Field(default=None, ge=1, le=1000)


@router.get("/limits")
async def get_commerce_limits() -> dict[str, Any]:
    services = get_commerce_services()
    return {name: services[name].limits.get_config(name).to_dict() for name in DEFAULT_LIMITS if name in services}


@router.put("/limits/{merchant}")
async def update_commerce_limits(merchant: str, body: LimitConfigBody) -> dict[str, Any]:
    merchant = str(merchant or "").strip().lower()
    service = _service(merchant)
    if body.daily_total < body.per_order:
        raise HTTPException(status_code=400, detail="daily_total_must_cover_per_order")
    # The order count policy is configured by the merchant; Meituan
    # keeps a finite count guard by default but permits an explicit update.
    try:
        config = service.limits.set_config(
            merchant, per_order=body.per_order, daily_total=body.daily_total,
            daily_orders=body.daily_orders,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "merchant": merchant, "limits": config.to_dict()}


@router.post("/{merchant}/submit")
async def submit_commerce_request(merchant: str, body: ShoppingSubmitBody) -> dict[str, Any]:
    service = _service(merchant)
    return service.submit_request({"brief": body.brief, "max_payable": body.max_payable, "address_label": body.address_label, "constraints": body.constraints})


@router.post("/meituan/device-preview")
async def preview_meituan_device() -> dict[str, Any]:
    """Bounded browsing diagnostics; no address, cart, order or payment mutation."""
    return await _service('meituan').preview_device()


@router.post("/{merchant}/proposal")
async def get_commerce_proposal(merchant: str, body: ProposalBody) -> dict[str, Any]:
    service = _service(merchant)
    return service.get_proposal(body.request_id, body.candidates)


@router.post("/{merchant}/confirm")
async def confirm_commerce_candidate(merchant: str, body: CandidateBody) -> dict[str, Any]:
    service = _service(merchant)
    return service.confirm_candidate(body.request_id, body.candidate_id, repeated_principle=body.repeated_principle, explicit_persistent=body.explicit_persistent)


@router.get("/{merchant}/status/{request_id}")
async def get_commerce_status(merchant: str, request_id: str) -> dict[str, Any]:
    return _service(merchant).get_status(request_id)


@router.post("/{merchant}/cancel")
async def cancel_commerce_request(merchant: str, body: RequestIdBody) -> dict[str, Any]:
    return _service(merchant).cancel_request(body.request_id)


@router.get("/action-requests")
async def list_action_requests() -> list[dict[str, Any]]:
    store = _store()
    result = store.list_pending()
    # Only already-masked first-read rows are exposed for rendering.  Raw
    # address/contact form values remain in the trusted volatile hook.
    for item in result:
        if item.get("kind") == "meituan_contact_confirmation":
            payload = store.get(item["request_id"], include_payload=True) or {}
            safe = payload.get("payload") or {}
            item["recognized_rows"] = list(safe.get("recognized_rows") or [])
            item["warning"] = safe.get("warning", "")
    return result


@router.get("/action-requests/{request_id}")
async def get_action_request(request_id: str) -> dict[str, Any]:
    result = _store().get(request_id, include_payload=False)
    if result is None:
        raise HTTPException(status_code=404, detail="action_request_not_found")
    if result.get("kind") == "meituan_contact_confirmation":
        payload = _store().get(request_id, include_payload=True) or {}
        safe = payload.get("payload") or {}
        result["recognized_rows"] = list(safe.get("recognized_rows") or [])
        result["warning"] = safe.get("warning", "")
    return result


@router.post("/action-requests/{request_id}/resolve")
async def resolve_action_request(request_id: str, body: ActionResolutionBody) -> dict[str, Any]:
    store = _store()
    action = store.get(request_id, include_payload=False)
    if action is None:
        raise HTTPException(status_code=404, detail="action_request_not_found")
    if action.get("status") != "pending":
        raise HTTPException(status_code=409, detail="action_request_not_pending")
    if body.confirmed:
        kind = str(action.get("kind") or "")
        if kind == "delivery_address" and (not body.place.strip() or not body.detail.strip()):
            raise HTTPException(status_code=400, detail="delivery_address_fields_required")
        if kind == "meituan_contact_confirmation" and (not body.name.strip() or not body.phone.strip()):
            raise HTTPException(status_code=400, detail="contact_fields_required")
        if kind not in {"delivery_address", "meituan_contact_confirmation", "contact_confirmation"}:
            raise HTTPException(status_code=400, detail="action_kind_not_resolvable")
    # Persist the terminal, schema-filtered outcome first.  Sensitive form
    # values enter the volatile host hook only after this succeeds.
    try:
        resolved = store.resolve(request_id, {"confirmed": bool(body.confirmed)})
    except ActionRequestTerminal as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if body.confirmed:
        # Values remain volatile and are available only to a host adapter in
        # this process.  The durable action row contains confirmation state,
        # not the address/contact itself.
        _volatile_action_values[request_id] = ({key: value for key, value in {"place": body.place, "detail": body.detail, "name": body.name, "phone": body.phone}.items() if value}, time.monotonic() + 300)
    else:
        _volatile_action_values.pop(request_id, None)
    return resolved


@router.post("/action-requests/{request_id}/cancel")
async def cancel_action_request(request_id: str) -> dict[str, Any]:
    _volatile_action_values.pop(request_id, None)
    try:
        return _store().cancel(request_id, reason="cancelled_by_user")
    except KeyError:
        raise HTTPException(status_code=404, detail="action_request_not_found")


@router.post("/address-requests")
async def create_address_request(task_id: str) -> dict[str, Any]:
    try:
        action = GpsAddressPolicy(action_store=_store()).create_elsewhere_request(task_id=task_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=type(exc).__name__)
    return action.to_dict()


@router.post("/contact/first-read")
async def contact_first_read(rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        action = _workflow().begin_first_read(rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"action_request": action.to_dict() if action else None, "cached": action is None}


@router.post("/contact/update")
async def contact_update(body: ContactUpdateBody) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="contact_confirmation_required")
    try:
        contact = _workflow().cache.set(name=body.name, phone=body.phone, user_confirmed=True)
    except RuntimeError as exc:
        # No OS credential backend means persistence is disabled.
        raise HTTPException(status_code=503, detail=str(exc))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "contact": contact.masked()}


@router.delete("/contact")
async def contact_clear() -> dict[str, Any]:
    try:
        _workflow().cache.clear()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"success": True, "cleared": True}


def get_volatile_action_values(request_id: str, *, consume: bool = False) -> dict[str, str] | None:
    """Trusted host-adapter hook; never exposed as an API/model result."""
    entry = _volatile_action_values.get(str(request_id))
    if entry is None:
        return None
    values, expires_at = entry
    if time.monotonic() >= expires_at:
        _volatile_action_values.pop(str(request_id), None)
        return None
    if consume:
        _volatile_action_values.pop(str(request_id), None)
    return dict(values) if values else None


__all__ = ["get_volatile_action_values", "router"]
