"""Remove echoed transport metadata only at outgoing natural-language boundaries."""
import re

_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_MODEL_BODY = r'生成模型[：:][ \t]*[^\r\n【】\[\]［］]{1,120}'
_MODEL_PREFIX = re.compile(rf'^ {{0,3}}(?:【{_MODEL_BODY}】|\[{_MODEL_BODY}\]|［{_MODEL_BODY}］)[ \t]*')


def strip_generated_model_label(content: str) -> str:
    """Strip reserved line-leading metadata, including labels attached to prose.

    Literal examples should be quoted or fenced; ordinary inline mentions stay.
    """
    text = str(content or '')
    lines = []
    fence_char, fence_size = '', 0
    removed = False
    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence_char:
            lines.append(line)
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char = ''
            continue
        if fence:
            fence_char, fence_size = fence[1][0], len(fence[1])
            lines.append(line)
        else:
            stripped_line = False
            while match := _MODEL_PREFIX.match(line):
                suffix = line[match.end():]
                following = suffix.lstrip(' \t')
                # Square-bracket labels can also be literal Markdown links.
                # Stacked registered headers are protocols, not references.
                if match[0].lstrip().startswith('['):
                    if following.startswith(('(', ':')):
                        break
                    if following.startswith('[') and not _MODEL_PREFIX.match(following):
                        from context_builder.display_markers import _PREFIX as display_prefix
                        if not display_prefix.match(following):
                            break
                removed = True
                stripped_line = True
                line = suffix
            if stripped_line and not line.strip():
                continue
            # A stripped prefix can reveal a code fence on the same line.
            fence = _FENCE.match(line)
            if fence:
                fence_char, fence_size = fence[1][0], len(fence[1])
            lines.append(line)
    return ''.join(lines).strip('\r\n') if removed else text
