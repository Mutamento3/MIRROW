"""Remove known rendered history prefixes without classifying message mehuman.

This boundary does not parse tool actions, voice payloads or Affect sidecars.
"""
import re
from reply_metadata_filter import _MODEL_PREFIX as _MODEL_HEADER_PREFIX
_HM = '(?:[01]?\\d|2[0-3]):[0-5]\\d'
_CLOCK = f'{_HM}(?::[0-5]\\d(?:\\.\\d{{1,6}})?)?'
_OFFSET = f'(?:[ \\t]*(?:Z|[+-](?:[01]\\d|2[0-3]):?[0-5]\\d))?'
_MONTH = '(?:0?[1-9]|1[0-2])'
_DAY = '(?:0?[1-9]|[12]\\d|3[01])'
_ABSOLUTE_TIME = f'(?:\\d{{4}}-{_MONTH}-{_DAY}(?:[ \\t]+|T){_CLOCK}{_OFFSET}|\\d{{4}}年{_MONTH}月{_DAY}日[ \\t]+{_CLOCK}{_OFFSET})'
_TIME = f'(?:{_ABSOLUTE_TIME}|{_MONTH}月{_DAY}日[ \\t]+{_CLOCK}{_OFFSET}|(?:昨天|今天|前天)[ \\t]+{_CLOCK}{_OFFSET}|{_CLOCK}{_OFFSET})'
_RECORD_LABELS = ('状态记录', '内在活动记录', '内在活动记录·漫想')
_RECORD_LABEL = '(?:' + '|'.join((re.escape(label) for label in _RECORD_LABELS)) + ')'
_MATERIAL_LABELS = ('状态记录·MIRROW系统提供，非聊天原话', '历史材料·MIRROW 状态记录', 'MIRROW 内在记录，非聊天原话')
_MATERIAL_LABEL = '(?:' + '|'.join((re.escape(label) for label in _MATERIAL_LABELS)) + ')'
_BODY = f'(?:(?:🛡\\ufe0f?|💭|⏰)(?:[ \\t]+{_TIME})?|{_ABSOLUTE_TIME}|{_TIME}[ \\t]*[｜|][ \\t]*{_RECORD_LABEL}|{_RECORD_LABEL}[ \\t]*[｜|][ \\t]*时间未记录|{_MATERIAL_LABEL})'
_PREFIX = re.compile(f'^(?:\\[{_BODY}\\]|【{_BODY}】|［{_BODY}］)')
_FENCE = re.compile('^ {0,3}(`{3,}|~{3,})(.*)$')

def strip_display_markers(content: str) -> str:
    """Strip known message/material prefixes; preserve prose and Markdown."""
    text = str(content or '')
    result = []
    fence_char = ''
    fence_size = 0
    for line in text.splitlines(keepends=True):
        opening = _FENCE.match(line)
        if fence_char:
            result.append(line)
            if opening and opening[1][0] == fence_char and (len(opening[1]) >= fence_size) and (not opening[2].strip()):
                fence_char = ''
            continue
        if opening:
            fence_char, fence_size = (opening[1][0], len(opening[1]))
            result.append(line)
            continue
        if line.startswith('\t') or line.startswith('    ') or line.lstrip(' ').startswith('>'):
            result.append(line)
            continue
        indent_size = len(line) - len(line.lstrip(' '))
        body = line[indent_size:]
        marker = _PREFIX.match(body)
        while marker:
            suffix = body[marker.end():]
            following = suffix.lstrip(' \t')
            if following.startswith(('(', ':')) or (following.startswith('[') and (not _PREFIX.match(following)) and (not _MODEL_HEADER_PREFIX.match(following))):
                break
            else:
                line = line[:indent_size] + suffix.lstrip(' \t')
                opening = _FENCE.match(line)
                if opening:
                    fence_char, fence_size = (opening[1][0], len(opening[1]))
                    break
                body = line[indent_size:]
                marker = _PREFIX.match(body)
        result.append(line)
    cleaned = ''.join(result)
    return cleaned.strip('\r\n') if cleaned.strip() else ''
