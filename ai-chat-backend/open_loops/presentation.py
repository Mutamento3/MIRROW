"""Short AI-facing prose; identities and selection reasons stay in audit."""


def concern_line(item, *, fading=False):
    text = str(item.get("content") or "").strip()
    progress = str(item.get('latest_progress') or '').strip()
    if progress:
        # The old sentence remains an exact tool reference, explicitly historical.
        if item.get('progress_source_current'):
            clock = str(item.get('fact_confirmed_at') or '')[:16].replace('T', ' ')
            text = f"最近进展（{clock}）：{progress}；此前留下的挂念原句：“{text}”"
        else:
            text = f"此前留下的挂念：{text}（近况原话已变更或暂不可核对）"
    return f"- {text}" + ("（很久没再留意，正慢慢淡出）" if fading else "")
