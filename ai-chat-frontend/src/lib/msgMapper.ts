import type { Message } from '../types';

/**
 * 后端 API 消息 → 前端 UI 消息统一映射。
 *
 * 收敛 App.tsx 7 处 + chat.tsx reloadCurrentSession 的内联映射，防字段漂移。
 * 2026-08-23：补 voiceAttachment（此前所有内联映射都漏掉，导致人类伙伴语音 reload 后气泡消失）。
 */
export function mapApiMessageToUi(apiMsg: any): Message {
  let toolCalls = apiMsg.toolCalls ?? apiMsg.tool_calls;
  if (typeof toolCalls === 'string') {
    try { toolCalls = JSON.parse(toolCalls); } catch { toolCalls = undefined; }
  }
  const eventType = apiMsg.eventType || apiMsg.event_type || undefined;
  
  
  return {
    id: apiMsg.id,
    role: apiMsg.role as Message['role'],
    content: apiMsg.content,
    timestamp: new Date(apiMsg.timestamp),
    rowOrder: apiMsg.rowOrder ?? undefined,
    thinkingChain: apiMsg.thinkingChain || undefined,
    reasoning_content: apiMsg.reasoning_content || undefined,
    modelSource: apiMsg.modelSource || apiMsg.model_source || undefined,
    toolCalls: Array.isArray(toolCalls) ? toolCalls : undefined,
    images: apiMsg.images || undefined,
    voiceAttachment: apiMsg.voiceAttachment || undefined,
    longTextCard: apiMsg.longTextCard || apiMsg.long_text_card || undefined,
    notificationMetadata: apiMsg.notificationMetadata || apiMsg.notification_metadata || apiMsg.metadata || undefined,
    eventType,
    eventParentId: apiMsg.eventParentId || apiMsg.event_parent_id || undefined,
    isWander: apiMsg.isWander ?? (apiMsg.role === 'event' ? false
      : ((typeof apiMsg.id === 'string' && apiMsg.id.startsWith('wander_')) || undefined)),
    isSentinel: apiMsg.isSentinel ?? ((typeof apiMsg.id === 'string' && apiMsg.id.startsWith('sentinel_')) || undefined),
    isReminder: apiMsg.isReminder ?? undefined,
    isMusicCochlea: apiMsg.isMusicCochlea ?? ((typeof apiMsg.id === 'string' && apiMsg.id.startsWith('mco_')) || undefined),
    isMusic: apiMsg.isMusic ?? ((typeof apiMsg.id === 'string' && apiMsg.id.startsWith('music_')) || undefined),
  };
}
