"""AI-facing presentation of admitted facts; canonical sources stay untouched."""
from typing import Mapping
from material_time import local_source_clock
INNER_RECORD_NAME = 'mirrow_inner_record'
INNER_SNAPSHOT_NAME = 'mirrow_inner_snapshot'
SECTION_LABELS = {'self_book': '内在快照·你的自我认识', 'emotion_self': '内在快照·你自身的心绪', 'emotion_resonance': '共振理解·人类伙伴的心情', 'memories': '回忆·此刻想起的经历', 'experience_recall': '回忆·经历留下的感受', 'world_book': '背景认识·人类伙伴的生活情况', 'cognition_book': '背景认识·你对人类伙伴的理解', 'time': '当前感知·时间', 'weather': '当前感知·天气', 'activity_summary': '当前感知·活动与在线情况', 'away': '当前感知·私聊间隔', 'sentinel_snapshot': '当前感知·哨兵与设备记录', 'recent_inner_wander': '近期漫想余韵'}

def snapshot_message(ingredient: str, content: str) -> dict:
    """Source-specific labels avoid turning inference into first-hand evidence."""
    label = SECTION_LABELS.get(ingredient, ingredient)
    if ingredient == 'memories':
        heading = '[你想起的相关经历｜本轮只呈现与眼前内容最相关的部分]'
        if content.startswith(heading):
            content = '[此刻想起的历史经历；可能相关、未确认同一次的记录另列]' + content[len(heading):]
    return {'role': 'assistant', 'name': INNER_SNAPSHOT_NAME, 'content': f'[{label}]\n{content}', '_section': ingredient}

def experience_text(value: Mapping) -> str:
    """Present the same selected materials and original feeling in AI's voice."""
    from memory_v2.experience_contract import project, impression_text
    if 'version' not in value and isinstance(value.get('execution_overview'), Mapping):
        from wander_manager.experience_presentation import runtime_text
        return runtime_text(value)
    material_view = dict(value)
    material_view['retained_impression'] = ''
    feeling = impression_text(value.get('retained_impression'), node_ids=value.get('retained_node_ids', []))
    materials = []
    for item in value.get('materials') or []:
        if item.get('kind') == 'xhs_interactions':
            from xhs_interactions.context import material
            item = material(item)
            if item.get('reflection') == feeling:
                item['reflection'] = ''
        materials.append(item)
    material_view['materials'] = materials
    from wander_manager.experience_presentation import frozen_text
    text = frozen_text(material_view)
    feeling = impression_text(value.get('retained_impression'), node_ids=value.get('retained_node_ids', []))
    if feeling:
        text += ('\n' if text else '') + '你当时留下的感想（原述）：「' + feeling + '」'
    return text

def internal_event_message(row: Mapping, content: str) -> dict:
    for before, after in (('AI 的漫想行动记录：', '你当时的漫想：'), ('AI 的漫想事件：', '你当时的漫想：'), ('[工具事件] AI', '[工具事实] 你'), ('[感知事件] AI', '[感知事实] 你'), ('[淘宝事件记录] ', '[你当时逛淘宝] ')):
        if content.startswith(before):
            content = after + content[len(before):]
            break
    clock = local_source_clock(row.get('timestamp'), seconds=True)
    label = {'wander_activity': '内在活动记录·漫想'}.get(row.get('event_type'), '内在活动记录')
    header = f'[{clock}｜{label}]' if clock else f'[{label}｜时间未记录]'
    return {'role': 'assistant', 'name': INNER_RECORD_NAME, 'content': header + '\n' + content}

def is_k_activity(row: Mapping) -> bool:
    return row.get('event_type') in {'wander_activity', 'tool_event'}

def fact_message(row: Mapping, content: str) -> dict:
    if is_k_activity(row):
        return internal_event_message(row, content)
    return {'role': 'system', 'content': content}

def covered_persona_anchor(persona: str, anchor: str) -> bool:
    return bool(anchor.strip() and anchor.strip() in persona)
