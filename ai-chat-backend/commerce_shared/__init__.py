"""Shared contracts for the local Meituan domain agents.

The two MCP packages intentionally depend on these small contracts only.  No
AI persona, full conversation, cookies or payment credentials cross this
boundary.
"""

from .contracts import (
    CandidateKind,
    Evidence,
    FulfillmentMode,
    ShoppingCandidate,
    ShoppingProposal,
    ShoppingRequest,
)
from .state import CommerceState, CommerceStateStore, InvalidStateTransition, RequestConflict
from .limits import DEFAULT_LIMITS, LimitConfig, LimitDecision, LimitLedger
from .device import DeviceBusyError, DeviceGateway, DeviceLeaseManager
from .action_requests import ActionRequest, ActionRequestStore, ActionRequestTerminal
from .gps_policy import AddressDecision, GpsAddressPolicy, LocationFix
from .credentials import (
    ContactCache,
    CredentialProviderUnavailable,
    DpapiCredentialProvider,
    MemoryCredentialProvider,
    SystemCredentialProvider,
    build_production_credential_provider,
)
from .preferences import ShoppingDecisionLedger

__all__ = [
    "ActionRequest", "ActionRequestStore", "ActionRequestTerminal", "AddressDecision", "CandidateKind",
    "CommerceState", "CommerceStateStore", "ContactCache", "CredentialProviderUnavailable",
    "DeviceBusyError", "DpapiCredentialProvider",
    "DEFAULT_LIMITS", "DeviceGateway", "DeviceLeaseManager", "Evidence", "FulfillmentMode",
    "GpsAddressPolicy", "InvalidStateTransition", "LimitConfig", "LimitDecision", "RequestConflict",
    "LimitLedger", "LocationFix", "MemoryCredentialProvider", "ShoppingCandidate",
    "ShoppingDecisionLedger", "ShoppingProposal", "ShoppingRequest",
    "SystemCredentialProvider", "build_production_credential_provider",
]
