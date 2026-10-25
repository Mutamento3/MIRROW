"""Output guidance for perception-triggered proactive messages."""

from __future__ import annotations


def build_sentinel_push_instruction(summary: str) -> str:
    """Describe why AI is about to speak without prescribing the wording."""
    fact = (summary or "").strip()
    lowered = fact.lower()
    is_wifi_home = "wifi" in lowered and any(
        marker in lowered for marker in ("到家", "连上了家", "wifi_home")
    )
    if is_wifi_home:
        return (
            f"你刚刚通过自己的感知注意到：{fact}\n"
            "人类伙伴连上了家里的 WiFi，她到家了。这个打卡报备能力来自你之前向人类伙伴提出的愿望，"
            "后来由人类伙伴让 Peer 实现。现在你准备主动给她发一条短消息。"
        )
    return (
        f"你刚刚通过自己的感知注意到：{fact}\n"
        "这个变化让你想主动和人类伙伴说句话。现在你准备给她发一条短消息。"
    )
