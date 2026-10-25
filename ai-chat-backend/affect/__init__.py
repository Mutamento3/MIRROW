"""MIRROW 的独立情绪 / 开放线索联动管道。

这个包只写独立的 ``events/affect.db``，也不改写生产 ``open_loops.db``。
Affect v2 默认 self/recognition 开启、resonance channel 关闭；普通聊天
在 quiet settlement 后才形成投影，活动收束可即时写入 AI self。
"""

from .store import AffectStore, get_affect_store
from .gate import AffectGate, GateDecision
from .parser import AffectValidationError, parse_flash_output, parse_flash_output_v2
from .sidecar import SidecarResult, bind_sidecar, parse_affect_sidecar, strip_affect_sidecar
from .service import AffectService, get_affect_service

__all__ = [
    "AffectStore",
    "get_affect_store",
    "AffectGate",
    "GateDecision",
    "AffectValidationError",
    "parse_flash_output",
    "parse_flash_output_v2",
    "SidecarResult",
    "parse_affect_sidecar",
    "strip_affect_sidecar",
    "bind_sidecar",
    "AffectService",
    "get_affect_service",
]
