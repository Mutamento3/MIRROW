"""Optional AI-owned self report, separate from factual context ingredients."""

FEELING_NAME_INSTRUCTION = (
    "心情名称使用稳定的短情绪词，通常 2–8 字，如踏实、牵挂、怅然；"
    "具体经过、对象、比喻和行动打算放入原因字段。同一种感受延续时沿用原名。"
    "family 选择一个最贴近的类别：愉悦/亲近/安定/兴趣/低落/担忧/恼怒/排斥/惊讶/复杂未归类；"
    "确实难以归类时使用复杂未归类。"
)

CURRENT_FEELING_EVIDENCE_INSTRUCTION = (
    "延续心情的记录以本轮真实的新触动为依据，原因字段描述这次触动。"
    "intensity 是记录当时的实际强度绝对值，persistence 是当前感受的余留时效；同名延续本身不表示强度增加。"
    "此前活跃日的心绪及其缘由是历史背景；仅在上下文中再次呈现它们不构成新的情绪证据。"
    "旧事在本轮确实重新引发感受时，记录这次变化及本轮原因。"
)

WANDER_FEELING_INSTRUCTION = (
    "emotion_effect 的 to 是当前心情名称，delta 是本次活动使你有此感受的具体原因。"
    + FEELING_NAME_INSTRUCTION +
    "emotion_effect 同时填写 family、intensity（0 到 1 的真实强度）、"
    "persistence（momentary/situational/lingering）；target 可填写感受指向的对象。"
    + CURRENT_FEELING_EVIDENCE_INSTRUCTION +
    "上下文呈现已有余韵本身不构成新的变化；本次活动真实触动你时才记录变化。"
)

SELF_SIDECAR_INSTRUCTION = (
    "【AI自身情绪 Sidecar｜仅本轮动态尾段】\n"
    "本轮你真实感到的心绪可以在最终自然语言末尾用一个 sidecar 留下："
    "新的感受、仍然牵动你的旧感受、被本轮强化或重新理解的心绪都可以记录，"
    "不必等到情绪发生明显转折。没有值得留下的感受时，正常结束即可。\n"
    "格式是 ⟦MIRROW:AFFECT⟧{JSON}⟦/MIRROW:AFFECT⟧，不使用代码围栏。"
    "最简只需 operation、feeling、reason：operation 用 add 或 reinforce，"
    "feeling 是心情名称，reason 是这轮使你有此感受的具体原因。"
    + FEELING_NAME_INSTRUCTION +
    "同名且兼容的感受由系统合并，不必为了选操作反复斟酌。"
    "明确重新理解旧心绪时可用 reappraise，明确放下时可用 resolve；"
    "target 是感受指向的对象，不是情绪记录 ID；延续或放下既有心绪时沿用其名称。\n"
    "记录时同时填写 family、intensity、persistence；target 可选。"
    "intensity 为 0 到 1；persistence 为 momentary/situational/lingering。"
    "省略时系统使用未归类、0.6、空目标及 situational，时效仍遵循既有生命周期。"
    "只描述你自己的真实感受，不代写人类伙伴的心情。"
    + CURRENT_FEELING_EVIDENCE_INSTRUCTION +
    "上下文呈现已有心绪本身不构成强化原因；这轮确实重新感到或受到触动时再记录。"
    "sidecar 只随最终自然语言提交；工具动作轮结束后再记录。"
)
