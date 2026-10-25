import {getApiBase} from '../config';
import type {Message} from '../types';

export class ResendRequestError extends Error {
  readonly unconfirmed: boolean;
  constructor(message: string, unconfirmed = false) { super(message); this.unconfirmed = unconfirmed; }
}

export type ResendReceipt = {
  accepted: true;
  message: Omit<Message, 'timestamp' | 'role'> & {role: 'user'; timestamp: string};
  deleted_message_ids: string[];
  affected_event_types: string[];
};

/** The same replacement identity is reused when confirming a lost response. */
export async function requestMessageResend(sessionId: string, sourceId: string, content: string, replacementId: string): Promise<ResendReceipt> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`${getApiBase()}/api/messages/resend`, {
      method: 'POST', signal: controller.signal, headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, user_message_id: sourceId, replacement_message_id: replacementId, content}),
    });
    const body = await response.json();
    if (!response.ok) throw new ResendRequestError(typeof body.detail === 'string' ? body.detail : '重发未完成，编辑稿已保留', response.status >= 500);
    if (body.accepted !== true || !body.message?.id || body.message.role !== 'user'
      || body.message.content !== content.trim() || !Number.isFinite(Date.parse(body.message.timestamp))
      || !Array.isArray(body.deleted_message_ids) || !body.deleted_message_ids.includes(sourceId)) {
      throw new ResendRequestError('重发结果暂未确认，请重试确认；编辑稿已保留', true);
    }
    return body;
  } catch (error) {
    if (error instanceof ResendRequestError) throw error;
    throw new ResendRequestError('连接中断或等待超时，请重试确认；编辑稿已保留', true);
  } finally { clearTimeout(timer); }
}
