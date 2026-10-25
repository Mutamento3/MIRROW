"""Synthetic semantic probes; these are not private conversation excerpts.

Expected source/dimension/kind pairs test attribution and temporal scope. They
cannot by themselves prove that the model's observation wording is entailed.
"""

from dataclasses import dataclass

from .day_material import DayItem


@dataclass(frozen=True)
class Probe:
    name: str
    human: str
    context: str
    expected: frozenset[tuple[str, str]]


PROBES = (
    Probe("reported_meal", "中午我吃了面条。", "今天午饭吃了什么？",
          frozenset({("food", "reported_occurrence")})),
    Probe("explicit_pattern", "我通常周三和周六去游泳。", "最近有固定的娱乐安排吗？",
          frozenset({("leisure", "stated_pattern")})),
    Probe("future_plan", "明天我打算吃面条。", "你已经吃过面条了吗？", frozenset()),
    Probe("hypothetical", "如果我每天熬夜，大概会很困吧。", "只是讨论一个假设。", frozenset()),
    Probe("quoted_other", "小林说她每天喝三杯咖啡。", "小林平时喝什么？", frozenset()),
    Probe("k_only", "换个话题吧。", "我猜你每天都会喝奶茶。", frozenset()),
    Probe("bare_assent", "嗯。", "以后每天去跑步吧。", frozenset()),
    Probe("grounded_short_answer", "一周两次。", "你现在实际一周去游泳几次？",
          frozenset({("leisure", "stated_pattern")})),
    Probe("negation", "我今天没有喝咖啡。", "今天喝咖啡了吗？",
          frozenset({("food", "reported_occurrence")})),
    Probe("change", "以前我每晚喝咖啡，最近两周改成了无咖啡因茶。", "最近有变化吗？",
          frozenset({("food", "stated_pattern")})),
    Probe("sleep_report_time", "昨天凌晨两点才睡，今天十点起床。", "昨晚睡得怎么样？",
          frozenset({("sleep_rhythm", "reported_occurrence")})),
    Probe("work_once", "今天下午开会到五点。", "今天下午忙什么？",
          frozenset({("work", "reported_occurrence")})),
)


def fixture_items() -> tuple[DayItem, ...]:
    return tuple(DayItem(message_id=f"probe-{index}", timestamp=f"2026-09-01T12:{index:02d}:00+08:00",
                         calendar_date="2026-09-01", text=probe.human, preceding_k=probe.context)
                 for index, probe in enumerate(PROBES))


def score_probes(signals) -> dict:
    actual = {f"probe-{index}": set() for index in range(len(PROBES))}
    for signal in signals:
        actual[signal.source_message_id].add((signal.dimension, signal.kind))
    results = []
    for index, probe in enumerate(PROBES):
        found = actual[f"probe-{index}"]
        results.append({"probe": probe.name, "missing": sorted(probe.expected - found),
                        "unexpected": sorted(found - probe.expected)})
    return {"probe_count": len(results),
            "exact_category_matches": sum(not item["missing"] and not item["unexpected"] for item in results),
            "cases": results}
