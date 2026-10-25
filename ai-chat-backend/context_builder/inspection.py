"""Describe the existing assembly without rendering ingredients a second time."""
from context_inspection import observe_build, redact
from types import SimpleNamespace

SOURCES = {
    'cognition_brief': ('领域认知画像', 'cognition.brief.read_context', '只读当前认知书的预生成领域投影；主体和领域由配方选择，实时状态、事件及详细认识仍来自各自原料。'),
    'other_core': ('他者书核心认识与旧关系兼容材料', 'cognition.other_legacy.core_context → cognition.other_book.context(core=True)', '只选本轮交互实体的有效核心词条；普通他者词条由 other_details 检索。完成认知迁移后不再注入旧画像/反馈。'),
    'cognitive_core': ('AI 的核心自我认识', 'cognition.books.core_context', '认知书的核心自我层，属于稳定身份材料。'),
    'other_details': ('他者书相关认识', 'cognition.other_book.context', '只为本轮实际交互者和当前话语明确提到的实体检索非核心认识；召回材料不扩张人物范围。'),
    'persona': ('人设正文', '调用方传入 persona', '本次调用提供的人设正文。'),
    'self_book': ('相关自我认识', 'Builder / 自我书检索结果', '按当前话语检索，不能以核心身份层替代。'),
    'conversation_messages': ('对话历史', 'Builder / conversation_messages 权威消息', '与状态、时间等独立消息共同按时间排列。'),
    'social_feed_reminder': ('朋友圈特别关心提醒', 'social_feed.unannounced_notifications', '只读 peek；成功回复落库后才消费本次通知。'),
}


def record_builder_inspection(builder, result):
    recipe = builder._inspection_recipe
    if recipe is None:
        return
    from context_builder.builder import dynamic_last_enabled, _DYNAMIC_LAST_RECIPES, resolve_section_placement
    split = bool(result.dynamic_content) or (
        dynamic_last_enabled() and builder._recipe_key in _DYNAMIC_LAST_RECIPES
    )
    messages = []
    for index, raw in enumerate(result.formatted_messages):
        message = {key: value for key, value in raw.items() if not key.startswith('_')}
        message['section'] = raw.get('_section') or ('system_content' if index == 0 and raw.get('role') == 'system' else 'unclassified')
        message['layer'] = 'stable_prefix' if message['section'] == 'system_content' and split else 'mixed_system' if message['section'] == 'system_content' else 'history_or_interleaved'
        messages.append(message)
    if result.dynamic_content:
        messages.append({'role':'system','content':result.dynamic_content,'section':'dynamic_content','layer':'dynamic'})
    for raw in getattr(builder, '_context_messages', []) or []:
        message = {key: value for key, value in raw.items() if not key.startswith('_')}
        message['section'] = raw.get('_section') or 'context_message'
        message['layer'] = 'context'
        messages.append(message)
    for message in result.tail_messages:
        messages.append(dict(message, section='social_feed_reminder' if builder._recipe_key == 'FULL_CHAT' else 'tail', layer='tail'))
    sections = []
    recipe_sections = list(recipe.sections)
    known = {section.ingredient for section in recipe_sections}
    recipe_sections.extend(SimpleNamespace(ingredient=name, name='当前自我状态' if name == 'self_awareness' else name, group='Builder 补充段', condition=None, placement=None)
                           for name in result.sections if name not in known)
    for position, section in enumerate(recipe_sections):
        name = section.ingredient
        value = dict(result.sections.get(name, {}))
        title, source, explanation = SOURCES.get(name, (section.name, 'context_builder.builder._call_ingredient / ' + name, '按当前配方解析的上下文原料；具体来源实现见所列入口。'))
        status = value.get('status', 'not_observed')
        text = redact(value.get('text') or '')
        placements = []
        if status not in {'gated', 'empty', 'error', 'disabled', 'shadow_preview', 'not_observed'}:
            for index, message in enumerate(messages):
                content = redact(message.get('content'))
                if message.get('section') == name:
                    placements.append({'message_index':index + 1,'start':0,'end':len(content) if isinstance(content,str) else None})
                elif text and isinstance(content,str) and message.get('section') in {'system_content','dynamic_content'}:
                    offset = content.find(text)
                    if offset >= 0 and content.find(text, offset+1) < 0:
                        placements.append({'message_index':index+1,'start':offset,'end':offset+len(text)})
        reasons = {'gated':'条件未通过，本轮未读取或注入此段。','empty':'原料函数本轮返回空值；这不等于整个数据源为空。','error':'原料构建失败；不可视为没有资料。','disabled':'引擎关闭，本轮未注入。','shadow_preview':'影子预览，不进入发送消息。','tail':'供调用方处理的尾部合同，Builder 消息内未证明落点。','not_observed':'没有段级记录，不能判断是否注入。'}
        diagnostics = builder._ingredient_diagnostics.get(name,{})
        if name not in SOURCES:
            source = diagnostics.get('source') or ('context_builder.builder._finalize' if name not in known else source)
        reason = reasons.get(status,'已构建，实际发送还需与 provider 消息核对。')
        if name == 'other_core' and status == 'empty' and diagnostics:
            reason = ('未选中有效交互实体。' if not diagnostics.get('selected_entities') else '选中实体没有产出核心正文或称呼资料。')
            if diagnostics.get('legacy_retired'):
                reason += '旧画像/反馈兼容通道已退役；非核心认识仍由 other_details 负责。'
        sections.append({**value,'ingredient':name,'name':title,'recipe_order':position+1,'group':section.group,'condition':section.condition,
                         'condition_value':bool(builder._flags.get(section.condition)) if section.condition else None,
                         'placement':resolve_section_placement(section),'stable_candidate':resolve_section_placement(section) == 'prefix','source':source,'explanation':explanation,
                         'reason':reason,'diagnostics':diagnostics,'placements':placements})
    observe_build(call_id=result.call_id,recipe=builder._recipe_key,scenario=builder._scenario_override or recipe.name,messages=messages,sections=sections,dynamic_last=split)
