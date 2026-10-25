/**
 * 会话管理API服务
 * 负责与后端会话API通信，实现跨端口聊天记录同步
 */

import type { ThinkingStep, ToolCallRecord, ImageAttachment, Message as UiMessage } from './types';
export type { ThinkingStep, ToolCallRecord, ImageAttachment };

export type ApiMessage = Omit<UiMessage, 'timestamp' | 'uiReceiptCarrier'> & {
  id: string;
  role: UiMessage['role'];
  content: string;
  timestamp: string; // ISO格式
  thinkingChain?: ThinkingStep[];
  toolCalls?: ToolCallRecord[];
  images?: ImageAttachment[];
  isWander?: boolean;
  isSentinel?: boolean;
  isMusic?: boolean;
  isGroupChatSummary?: boolean;
  summaryText?: string;
  summaryAt?: string;
  reasoning_content?: string;
  isReminder?: boolean;
  eventType?: string;
};

export type ApiSession = {
  id: string;
  title: string;
  messages: ApiMessage[];
  createdAt: string; // ISO格式
  updatedAt: string; // ISO格式
  locked?: boolean;
  isPrimary?: boolean;
  // 消息窗口元数据：messages 可能只是「最近 N 条」的窗口，而非整个会话历史。
  hasMore?: boolean;   // true = 服务端还有更早的消息
  oldestId?: string;   // 翻页游标：窗口内最早一条的 id
  oldestTs?: string;   // 游标 id 失效时的兜底比较值
  oldestRowId?: number;
  total?: number;      // 仅单会话端点返回
  cursorStale?: boolean; // 游标已失效，应重置游标并重拉尾窗
};

/** getSession 的窗口 / 翻页参数。不传 limit 时后端保持全量返回（兼容旧客户端）。 */
export type SessionMessageQuery = {
  limit?: number;
  /** 翻页游标：取该 message_id 之前的一页 */
  beforeId?: string;
  /** 游标消息已被撤回时的兜底比较值 */
  beforeTs?: string;
  beforeRowId?: number;
};

export type SessionListResponse = {
  sessions: ApiSession[];
  current_session_id: string;
};

import { getApiBase, getPlatform } from './config';

const API_BASE_URL = getApiBase();

export async function getChatTurnStatus(sessionId: string, userMessageId: string): Promise<import('./lib/chatTurnRecovery').ChatTurnStatus> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${getApiBase()}/api/sessions/${encodeURIComponent(sessionId)}/chat-turn-status?user_message_id=${encodeURIComponent(userMessageId)}`, {
      signal: controller.signal, cache: 'no-store',
    });
    if (!response.ok) throw new Error(`获取轮次状态失败: ${response.status}`);
    const data = await response.json();
    if (data.session_id !== sessionId || data.user_message_id !== userMessageId ||
        !['running', 'completed', 'failed', 'unknown'].includes(data.status)) {
      throw new Error('轮次状态不匹配');
    }
    return data.status;
  } finally {
    window.clearTimeout(timeout);
  }
}

/**
 * 获取所有会话
 */
export async function getAllSessions(signal?: AbortSignal, limit?: number): Promise<SessionListResponse> {
  const url = limit ? `${API_BASE_URL}/api/sessions?limit=${limit}` : `${API_BASE_URL}/api/sessions`;
  const t0 = performance.now();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 15000);
  console.log(`[getAllSessions] >>> FETCH url=${url} platform=${getPlatform()}`);
  try {
    const response = await fetch(url, { signal: controller.signal });
    const tt = (performance.now() - t0).toFixed(0);
    const contentLength = response.headers.get('content-length') || 'unknown';
    console.log(`[getAllSessions] <<< STATUS=${response.status} content-length=${contentLength} time_ms=${tt}`);
    if (!response.ok) {
      throw new Error(`获取会话失败: ${response.status} ${response.statusText}`);
    }
    const t1 = performance.now();
    const data = await response.json();
    const parseMs = (performance.now() - t1).toFixed(0);
    console.log(`[getAllSessions] JSON parsed sessions=${data.sessions?.length ?? 0} currentId=${data.current_session_id} parse_ms=${parseMs}`);
    if (data.sessions && data.sessions.length > 0) {
      console.log(`[getAllSessions] First session: id=${data.sessions[0].id} title=${data.sessions[0].title} msgs=${data.sessions[0].messages?.length ?? 0}`);
    } else {
      console.warn(`[getAllSessions] WARNING: sessions array is EMPTY!`);
    }
    return data;
  } catch (error) {
    const tt = (performance.now() - t0).toFixed(0);
    if (error instanceof DOMException && error.name === 'AbortError') {
      console.log(`[getAllSessions] ABORTED after ${tt}ms`);
      throw error;
    }
    console.error(`[getAllSessions] FAILED after ${tt}ms:`, error);
    // 由 App 的重试/恢复层区分“网络暂时不可达”和“后端真的没有会话”。
    // 返回空数组会让一次短断线清空整个侧边栏。
    throw error;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

/**
 * 获取特定会话
 */
export async function getSession(sessionId: string, query: SessionMessageQuery = {}): Promise<ApiSession | null> {
  const params = new URLSearchParams();
  if (query.limit) params.set('limit', String(query.limit));
  if (query.beforeId) params.set('before_id', query.beforeId);
  if (query.beforeTs) params.set('before_ts', query.beforeTs);
  if (query.beforeRowId) params.set('before_row_id', String(query.beforeRowId));
  const qs = params.toString();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}${qs ? `?${qs}` : ''}`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      throw new Error(`获取会话失败: ${response.status} ${response.statusText}`);
    }
    return await response.json();
  } catch (error) {
    console.error(`获取会话 ${sessionId} 失败:`, error);
    return null;
  } finally {
    window.clearTimeout(timeout);
  }
}

/** 轻量计数与缓存版本检查，不传输消息正文。 */
export async function getSessionMessageCount(sessionId: string, signal?: AbortSignal): Promise<{
  total: number; updatedAt: string;
}> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = window.setTimeout(abort, 10000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/messages/count`, {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`获取消息总数失败: ${response.status}`);
    return response.json();
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

export type MessageSearchCursor = { timestamp: string; row_id: number };
export type MessageCardSearchType = 'card' | 'card_post' | 'card_music' | 'card_long_text' | 'card_taobao' | 'card_wardrobe' | 'card_report' | 'card_other';
export type MessageSearchType = 'all' | 'text' | 'image' | 'voice' | 'event' | 'tool' | MessageCardSearchType;
export type MessageSearchHit = {
  id: string; role: string; timestamp: string; snippet: string;
  types?: Exclude<MessageSearchType, 'all'>[]; snippet_source?: string;
};
export type MessageSearchPage = {
  items: MessageSearchHit[];
  has_more: boolean;
  next_cursor: MessageSearchCursor | null;
};

export async function searchSessionMessages(
  sessionId: string, query: string, cursor?: MessageSearchCursor | null, signal?: AbortSignal,
  messageType: MessageSearchType = 'all',
): Promise<MessageSearchPage> {
  const params = new URLSearchParams({ q: query, limit: '50', message_type: messageType });
  if (cursor) {
    params.set('before_ts', cursor.timestamp);
    params.set('before_row_id', String(cursor.row_id));
  }
  const response = await fetch(`${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/messages/search?${params}`, { signal });
  if (!response.ok) throw new Error(`搜索消息失败: ${response.status}`);
  return response.json();
}

export async function getMessagesAround(sessionId: string, messageId: string): Promise<{
  messages: ApiMessage[]; hasMoreBefore: boolean; hasMoreAfter: boolean;
}> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/messages/around/${encodeURIComponent(messageId)}`);
  if (!response.ok) throw new Error(`定位消息失败: ${response.status}`);
  return response.json();
}

export async function getMessagesAfter(sessionId: string, afterId: string, afterTs: string, afterRowId?: number): Promise<{
  messages: ApiMessage[]; hasMore: boolean; cursorStale: boolean; newestId: string; newestTs: string; newestRowId: number;
}> {
  const params = new URLSearchParams({ after_id: afterId, after_ts: afterTs, limit: '200' });
  if (afterRowId) params.set('after_row_id', String(afterRowId));
  const response = await fetch(`${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/messages/after?${params}`);
  if (!response.ok) throw new Error(`加载较新消息失败: ${response.status}`);
  return response.json();
}

/**
 * 创建新会话
 */
export async function createSession(id?: string, title?: string, messages?: Array<ApiMessage | Record<string, unknown>>): Promise<ApiSession> {
  try {
    const requestData: any = {};
    if (id) requestData.id = id;
    if (title) requestData.title = title;
    if (messages) requestData.messages = messages;

    const response = await fetch(`${API_BASE_URL}/api/sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestData),
    });

    if (!response.ok) {
      throw new Error(`创建会话失败: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('创建会话失败:', error);
    // 返回一个默认会话对象（保留传入的 id，确保调用方 ID 一致）
    const now = new Date().toISOString();
    const fallbackMessages = (messages ?? []).filter((message): message is ApiMessage => (
      typeof message.id === 'string' && typeof message.content === 'string' && typeof message.timestamp === 'string'
    ));
    return {
      id: id || Date.now().toString(),
      title: title || '新会话',
      messages: fallbackMessages,
      createdAt: now,
      updatedAt: now,
    };
  }
}

/**
 * 更新会话
 */
export async function updateSession(
  sessionId: string,
  updates: { title?: string; messages?: Array<ApiMessage | Record<string, unknown>>; locked?: boolean }
): Promise<ApiSession | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updates),
    });

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      throw new Error(`更新会话失败: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`更新会话 ${sessionId} 失败:`, error);
    return null;
  }
}

/**
 * 删除会话
 */
export async function deleteSession(sessionId: string): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      if (response.status === 404) {
        return false;
      }
      throw new Error(`删除会话失败: ${response.status} ${response.statusText}`);
    }

    return true;
  } catch (error) {
    console.error(`删除会话 ${sessionId} 失败:`, error);
    return false;
  }
}

/**
 * 切换会话锁定状态
 */
export async function toggleSessionLock(sessionId: string): Promise<{ session_id: string; locked: boolean } | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/lock`, {
      method: 'POST',
    });

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      throw new Error(`切换锁定状态失败: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`切换锁定状态 ${sessionId} 失败:`, error);
    return null;
  }
}

/**
 * 设置当前会话
 */
export async function setCurrentSession(sessionId: string): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/current/${sessionId}`, {
      method: 'POST',
    });

    if (!response.ok) {
      if (response.status === 404) {
        return false;
      }
      throw new Error(`设置当前会话失败: ${response.status} ${response.statusText}`);
    }

    return true;
  } catch (error) {
    console.error(`设置当前会话 ${sessionId} 失败:`, error);
    return false;
  }
}

/**
 * 获取当前会话ID
 */
export async function getCurrentSessionId(): Promise<string> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/current/id`);
    if (!response.ok) {
      throw new Error(`获取当前会话ID失败: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    return data.current_session_id || '';
  } catch (error) {
    console.error('获取当前会话ID失败:', error);
    return '';
  }
}

/**
 * 清除所有会话
 */
export async function clearAllSessions(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/sessions/clear`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ confirm: true }),
    });

    if (!response.ok) {
      throw new Error(`清除所有会话失败: ${response.status} ${response.statusText}`);
    }

    return true;
  } catch (error) {
    console.error('清除所有会话失败:', error);
    return false;
  }
}

/**
 * 检查会话服务健康状态
 */
export async function checkSessionsHealth(): Promise<{ status: string; sessions_count: number }> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health/sessions`);
    if (!response.ok) {
      throw new Error(`检查健康状态失败: ${response.status} ${response.statusText}`);
    }
    return await response.json();
  } catch (error) {
    console.error('检查会话服务健康状态失败:', error);
    return { status: 'unreachable', sessions_count: 0 };
  }
}

/**
 * 将Date对象转换为ISO字符串，确保一致的时间格式
 */
export function dateToISO(date: Date): string {
  return date.toISOString();
}

/**
 * 从ISO字符串创建Date对象
 */
export function isoToDate(isoString: string): Date {
  return new Date(isoString);
}
