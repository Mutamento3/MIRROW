"""Lightweight source decision: local physical PC input, not guessed proximity."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pc_capture import Microphone

PC_ENTER_IDLE_SECONDS = 90
PC_STAY_IDLE_SECONDS = 150


def input_diagnostics() -> dict:
    from silicon_perception.collection.raw_input_origin import get_input_origin_tracker
    return get_input_origin_tracker().diagnostics()


def configured_microphone() -> str:
    from settings_manager import get_setting
    return str(get_setting("ambient_pc_microphone") or "").strip()


def computer_candidate(current_source: str) -> tuple[Microphone | None, str]:
    configured = configured_microphone()
    if not configured:
        return None, "pc_microphone_not_selected"
    try:
        from mobile_relay import get_mobile_relay_registry
        native = get_mobile_relay_registry().live_entries(client_kind="native_service")
        if native and not any("ambient_source_lease_v1" in entry.capabilities
                              for entry in native):
            return None, "phone_source_lease_unsupported"
    except Exception:
        return None, "phone_source_lease_unavailable"
    try:
        from silicon_perception.collection.raw_input_origin import (
            InputOrigin, get_input_origin_tracker,
        )
        tracker = get_input_origin_tracker()
        tracker.start(wait_seconds=0)
        state = tracker.snapshot()
        idle = state.physical_input_idle
        limit = PC_STAY_IDLE_SECONDS if current_source == "pc" else PC_ENTER_IDLE_SECONDS
        if not state.available:
            return None, "pc_input_origin_unavailable"
        if idle is None:
            return None, "pc_physical_input_unconfirmed"
        if idle > limit:
            return None, "no_recent_local_pc_input"
        if (state.last_origin is InputOrigin.REMOTE and state.remote_input_idle is not None
                and state.remote_input_idle < idle):
            return None, "remote_pc_input_not_local_presence"
    except Exception:
        return None, "pc_input_origin_unavailable"
    from .pc_capture import selected_microphone
    microphone = selected_microphone(configured)
    if microphone is None:
        return None, "pc_microphone_unavailable"
    return microphone, "recent_local_pc_input"
