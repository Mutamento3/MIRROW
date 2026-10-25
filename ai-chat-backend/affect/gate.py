"""情绪候选信号门。

v1 使用确定性规则做高召回候选筛选，并保留可选的语义提示接口。
规则只回答“是否值得让 Flash 看一眼”，绝不直接修改共振状态。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional


# 第一人称自述的常见形式。词表不是情绪本体，只是候选召回器。
_STATE_WORDS = (
    "烦", "难受", "难过", "伤心", "开心", "高兴", "快乐", "兴奋", "累", "疲惫", "困",
    "生气", "气死", "委屈", "焦虑", "害怕", "担心", "崩溃", "撑不住", "麻木", "空落",
    "孤单", "孤独", "无聊", "平静", "放松", "安心", "期待", "失望", "绝望", "恶心",
    "后悔", "尴尬", "羞", "爽", "满足", "愉快", "不舒服", "不爽", "不耐烦",
)
_FIRST_PERSON = r"(?:我|俺|本人|老子|咱|自己)"
_NEGATION = ("不", "没", "没有", "不是", "并不", "并没有", "并非", "未")
_CORRECTION_MARKERS = ("其实", "不是", "只是", "现在好了", "已经好了", "我刚才不是", "你误会了", "更准确地说")
_OBJECT_PREFIXES = ("这个", "那个", "这件事", "这东西", "它", "功能", "按钮", "bug", "代码", "老板", "她", "他", "别人")


@dataclass(frozen=True)
class GateDecision:
    source_id: str
    triggered: bool
    reason: str
    matched_pattern: str = ""
    semantic_hint: bool = False
    semantic_score: Optional[float] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_first_person_state(text: str) -> tuple[bool, str, dict[str, Any]]:
    """识别自述、否定、修正和对象评价，返回诊断而不是情绪结论。"""
    compact = re.sub(r"\s+", "", text or "")
    diagnostics: dict[str, Any] = {
        "first_person": False,
        "state_word": "",
        "negated": False,
        "correction": False,
        "object_anchored": False,
    }
    if not compact:
        return False, "empty", diagnostics

    # 引用他人/转述不是人类伙伴的自述；保守地交给 Flash 仍可由显式接口触发。
    quoted_other = bool(re.search(r"(?:她|他|别人|朋友|老板)说.{0,12}(?:我|他|她).{0,10}(?:" + "|".join(_STATE_WORDS) + ")", compact))
    if quoted_other:
        diagnostics["quoted_other"] = True
        return False, "quoted_other", diagnostics

    first = re.search(_FIRST_PERSON, compact)
    if not first:
        return False, "no_first_person", diagnostics
    diagnostics["first_person"] = True
    prefix = compact[first.end(): first.end() + 24]
    for word in _STATE_WORDS:
        pos = prefix.find(word)
        if pos < 0:
            continue
        diagnostics["state_word"] = word
        before = prefix[max(0, pos - 5):pos]
        negated = any(before.endswith(neg) for neg in _NEGATION)
        diagnostics["negated"] = negated
        diagnostics["correction"] = any(marker in compact for marker in _CORRECTION_MARKERS)
        # “这个 bug 很烦”/“老板让我很烦”不是整体情绪自述；其中“我很烦”仍会通过。
        earlier = compact[max(0, first.start() - 8): first.start()]
        object_anchored = any(prefix.startswith(obj) for obj in _OBJECT_PREFIXES)
        diagnostics["object_anchored"] = object_anchored
        if object_anchored and not diagnostics["correction"]:
            return False, "object_evaluation", diagnostics
        if negated or diagnostics["correction"]:
            return True, "explicit_correction", diagnostics
        return True, "explicit_first_person_state", diagnostics
    return False, "no_state_word", diagnostics


class AffectGate:
    """只做候选召回，允许调用方注入已有向量检索结果。"""

    def __init__(self, *, semantic_threshold: float = 0.78):
        self.semantic_threshold = float(semantic_threshold)

    def evaluate(
        self,
        text: str,
        *,
        source_id: str,
        semantic_score: Optional[float] = None,
        semantic_hint: bool = False,
        semantic_label: str = "",
    ) -> GateDecision:
        if not source_id:
            raise ValueError("gate requires a backend-generated source_id")
        explicit, reason, diagnostics = _has_first_person_state(text)
        if explicit:
            return GateDecision(
                source_id=source_id,
                triggered=True,
                reason=reason,
                matched_pattern=diagnostics.get("state_word", ""),
                diagnostics=diagnostics,
            )
        score = float(semantic_score) if semantic_score is not None else None
        semantic_trigger = bool(semantic_hint and score is not None and score >= self.semantic_threshold)
        if semantic_trigger:
            diagnostics.update({"semantic_label": semantic_label, "semantic_threshold": self.semantic_threshold})
            return GateDecision(
                source_id=source_id,
                triggered=True,
                reason="semantic_hint",
                matched_pattern=semantic_label,
                semantic_hint=True,
                semantic_score=score,
                diagnostics=diagnostics,
            )
        return GateDecision(
            source_id=source_id,
            triggered=False,
            reason=reason,
            semantic_hint=bool(semantic_hint),
            semantic_score=score,
            diagnostics=diagnostics,
        )

    def preview(self, text: str, *, source_id: str = "preview", semantic_score: Optional[float] = None,
                semantic_hint: bool = False, semantic_label: str = "") -> dict[str, Any]:
        """只读诊断用入口。"""
        return self.evaluate(
            text,
            source_id=source_id or "preview",
            semantic_score=semantic_score,
            semantic_hint=semantic_hint,
            semantic_label=semantic_label,
        ).to_dict()


__all__ = ["AffectGate", "GateDecision"]
