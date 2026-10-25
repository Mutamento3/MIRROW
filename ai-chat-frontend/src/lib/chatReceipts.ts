import type { Message, ToolCallRecord } from '../types';

/** Stream updates own chat rows, not concurrently arriving persisted pushes. */
export function mergeStreamNotifications(current: Message[], incoming: Message[]): Message[] {
  const notices = new Map(current.filter(m => m.role === 'notification' || m.role === 'event'
    || (m.role === 'assistant' && (m.isReminder || m.isWander || m.isSentinel || m.isMusicCochlea
      || m.id.startsWith('wander_workshop_share_'))))
    .map(m => [m.id, m]));
  const merged = incoming.map(m => notices.get(m.id) || m);
  const ids = new Set(merged.map(m => m.id));
  for (const notice of notices.values()) if (!ids.has(notice.id)) merged.push(notice);
  return merged.sort((a,b) => a.timestamp.getTime() - b.timestamp.getTime());
}

export const hiddenBandReceipt = (call: ToolCallRecord) => call.tool === 'band_touch'
  && Boolean(call.extra_data?.band_notice_id || call.extra_data?.ui_receipt_pending);

/** Resolve only this turn's provisional records; never deduplicate by note text. */
export function settleBandReceipts(messages: Message[], completed?: ToolCallRecord[]): Message[] {
  const bands = (completed || []).filter(call => call.tool === 'band_touch');
  const used = new Set<number>();
  const key = (call: ToolCallRecord) => JSON.stringify(['action','note','pattern'].map(name => call.parameters?.[name] ?? null));
  return messages.map(message => ({...message, toolCalls: message.toolCalls?.map(call => {
    if (call.tool !== 'band_touch' || !call.extra_data?.ui_receipt_pending) return call;
    const index = bands.findIndex((candidate,index) => !used.has(index) && key(candidate) === key(call));
    const final = bands[index];
    if (index >= 0) used.add(index);
    const extra = {...call.extra_data};
    delete extra.ui_receipt_pending;
    return final ? {...final} : {...call, extra_data: extra};
  })}));
}

export function receiptOnlyRow(message: Message): boolean {
  return message.role === 'assistant' && !message.content?.trim()
    && !message.images?.length && !message.voiceAttachment && !message.longTextCard
    && !message.thinkingChain?.length && !message.reasoning_content
    && (Boolean(message.toolCalls?.length) || message.uiReceiptCarrier === true)
    && (message.toolCalls || []).every(call => call.tool === 'band_touch');
}

export const hiddenReceiptRow = (message: Message) => receiptOnlyRow(message)
  && (message.toolCalls || []).every(hiddenBandReceipt);

/** Derive visible event rows from the one authoritative message/toolCalls copy. */
export function projectToolEventRows(messages: Message[]): Message[] {
  const seen = new Set(messages.filter(m => m.role === 'event').map(m => m.id));
  const rows: Message[] = [];
  for (const message of messages) {
    
    if (message.role === 'event') {
      rows.push(message);
      continue;
    }
    const retained: ToolCallRecord[] = [];
    for (const call of message.toolCalls || []) {
      const eventId = typeof call.event_id === 'string' ? call.event_id : '';
      const occurred = typeof call.completed_at === 'string' ? new Date(call.completed_at) : null;
      const extra = call.extra_data || {};
      const specialized = call.tool === 'send_voice'
        || call.tool === 'ambient_event_link' || call.tool === 'band_touch'
        || Boolean(extra.lounge_visit || extra.music_card || extra.attachment_kind === 'music'
          || extra.xhs_comment);
      if (!eventId.startsWith('tool_event:') || !occurred || Number.isNaN(occurred.getTime()) || specialized) {
        retained.push(call);
        continue;
      }
      if (!seen.has(eventId)) {
        seen.add(eventId);
        rows.push({
          id: eventId,
          role: 'notification',
          eventType: 'tool_event',
          content: '',
          timestamp: occurred,
          toolCalls: [call],
        });
      }
    }
    const extracted = retained.length !== (message.toolCalls || []).length;
    const carrier = !extracted
      ? message : { ...message, toolCalls: retained.length ? retained : undefined };
    if (!extracted || carrier.role !== 'assistant' || carrier.content?.trim() || carrier.images?.length
      || carrier.voiceAttachment || carrier.longTextCard || carrier.thinkingChain?.length
      || carrier.reasoning_content || carrier.toolCalls?.length) {
      rows.push(carrier);
    }
  }
  return rows.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
}
