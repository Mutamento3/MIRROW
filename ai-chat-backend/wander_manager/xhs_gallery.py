"""Shared bounded gallery traversal; captured pages are not analyzed pages."""
import re


def position(root):
    found = {tuple(map(int, m.groups())) for n in root.iter('node')
             if (m := re.search(r'图片,第(\d+)张,共(\d+)张', n.get('content-desc', '')))}
    if len(found) != 1:
        return None
    current, total = next(iter(found))
    return (current, total) if 1 <= current <= total else None


async def collect(snapshot, *, get_position, capture, advance, refresh, limit=12):
    """Capture only consecutively proven pages; never infer a missing page."""
    pos = get_position(snapshot)
    await capture(snapshot, 'initial')
    complete = False
    if pos:
        current, total = pos
        first = current == 1
        for _ in range(max(0,min(total, limit)-1)):
            if current == total:
                complete = first
                break
            await advance()
            snapshot = await refresh()
            if get_position(snapshot) != (current + 1, total):
                break
            current += 1
            await capture(snapshot, f'image_{current}_of_{total}')
        complete = first and current == total
    return snapshot, complete
