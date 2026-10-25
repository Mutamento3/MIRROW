import { createContext, useContext, useState, useRef, useLayoutEffect, useCallback } from 'react';
import type { ReactNode, Dispatch, SetStateAction, MutableRefObject } from 'react';
import type { Session, ToolCallStatus, Message } from '../types';
import * as sessionApi from '../sessionApi';
import { mapApiMessageToUi } from '../lib/msgMapper';
import { mergeStreamNotifications } from '../lib/chatReceipts';
import { upsertOutgoingMessage } from '../lib/outgoingMessage';

// 聊天核心态 Context —— 承载 sessions / 当前会话 / 加载态 / 工具状态 / 流式刷新，
// 以及 currentSessionIdRef 镜像 + 核心操作（会话 CRUD + 消息写 + 重新加载），
// 供 WS hooks 和功能面板消费，消除 props 层层下传。
// 注意：sessionsRef / prevLoadingRef / isLoadingRef 仍留在 App——prevLoadingRef 在 App 里
// 有「读旧值→写新值」的加载结束检测模式，不能提前到 Provider 同步，否则破坏该检测。

type ChatContextValue = {
  sessions: Session[];
  setSessions: Dispatch<SetStateAction<Session[]>>;
  currentSessionId: string;
  setCurrentSessionId: Dispatch<SetStateAction<string>>;
  isLoading: boolean;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  inputActivities: Record<string, { kind: string; label: string }>;
  beginInputActivity: (kind: string, label: string) => string;
  updateInputActivity: (token: string, kind: string, label: string) => void;
  endInputActivity: (token: string) => void;
  toolCallStatus: ToolCallStatus[];
  setToolCallStatus: Dispatch<SetStateAction<ToolCallStatus[]>>;
  streamingTick: number;
  setStreamingTick: Dispatch<SetStateAction<number>>;
  currentSessionIdRef: MutableRefObject<string>;
  addSession: () => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  toggleSessionLock: (sessionId: string) => Promise<void>;
  switchSession: (sessionId: string) => Promise<void>;
  updateSessionMessages: (sessionId: string, newMessages: Message[], options?: { preserveNotifications?: boolean }) => Promise<void>;
  upsertSessionMessage: (sessionId: string, message: Message) => void;
  reloadCurrentSession: (options?: { authoritative?: boolean }) => Promise<void>;
  recordRemovedMessages: (sessionId: string, messageIds: string[]) => void;
  restoreRemovedMessages: (sessionId: string, messageIds: string[]) => void;
  reconcileServerMessages: (sessionId: string, current: Message[], server: Message[], preserveLocal?: boolean, hasMore?: boolean) => Message[];
};

/** 服务端尾部窗口大小，与后端 DEFAULT_MESSAGE_WINDOW 对齐。日常路径只加载这么多。 */
export const MESSAGE_WINDOW = 200;

const byTimestamp = (a: Message, b: Message) => {
  const timeOrder = a.timestamp.getTime() - b.timestamp.getTime();
  return timeOrder || (a.rowOrder != null && b.rowOrder != null ? a.rowOrder - b.rowOrder : 0);
};

const ChatContext = createContext<ChatContextValue | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);
  const isLoadingRef = useRef(isLoading);
  isLoadingRef.current = isLoading;
  const [inputActivities, setInputActivities] = useState<Record<string, { kind: string; label: string }>>({});
  const activitySeqRef = useRef(0);
  const [toolCallStatus, setToolCallStatus] = useState<ToolCallStatus[]>([]);
  const [streamingTick, setStreamingTick] = useState(0);
  // A GET started before a revoke can finish afterward. Keep exact IDs
  // suppressed for this app lifetime so stale snapshots cannot resurrect them.
  const removedMessageIdsRef = useRef<Map<string, Set<string>>>(new Map());

  const recordRemovedMessages = (sessionId: string, messageIds: string[]) => {
    const removed = removedMessageIdsRef.current.get(sessionId) || new Set<string>();
    messageIds.forEach(id => removed.add(id));
    removedMessageIdsRef.current.set(sessionId, removed);
  };

  const restoreRemovedMessages = (sessionId: string, messageIds: string[]) => {
    const removed = removedMessageIdsRef.current.get(sessionId);
    messageIds.forEach(id => removed?.delete(id));
    if (removed?.size === 0) removedMessageIdsRef.current.delete(sessionId);
  };

  const reconcileServerMessages = (
    sessionId: string,
    current: Message[],
    server: Message[],
    preserveLocal = false,
    hasMore = true,
  ): Message[] => {
    const removed = removedMessageIdsRef.current.get(sessionId);
    const validServer = server.filter(message => !removed?.has(message.id));
    const serverIds = new Set(validServer.map(message => message.id));
    // 本地有、服务端这次没返回、且没被本机标记删除的消息
    const isLocalOnly = (message: Message) => !serverIds.has(message.id) && !removed?.has(message.id);

    // 1) 并集：显式要求保留本地，或正在流式 / 乐观渲染
    if (preserveLocal) {
      return [...current.filter(isLocalOnly), ...validServer].sort(byTimestamp);
    }

    // 2) 严格白名单：服务端返回的就是完整历史（小会话，或 authoritative 权威同步）。
    //    这一支必须保持原有行为 —— 撤回后本地残留的消息靠它清掉。
    if (!hasMore || validServer.length === 0) {
      return [
        ...current.filter(message => isLocalOnly(message) && message.role === 'notification'),
        ...validServer,
      ].sort(byTimestamp);
    }

    // 3) 区间合并：服务端只返回了尾部窗口（hasMore=true），窗口锚点之前属于
    //    「本机已经翻出来的更早历史」—— 这次没被返回，绝不能删。
    //
    //    代价（有意接受）：若另一台设备删除了落在该区间里的消息，而本机没有
    //    removedMessageIdsRef 记录，它会作为幽灵保留，直到用户翻过那一段被重新
    //    校验清掉。失败模式是「多显示一条旧消息」，换来的是手机不再被全量加载
    //    拖死；不要为了消除幽灵把这里改回全量拉取。
    const anchor = validServer[0];
    const anchorTime = anchor.timestamp.getTime();
    // 用 <= 而非 <：锚点常与同秒的兄弟行并列（生产数据中单个 timestamp 最多挤过
    // 19 行），闭区间偏向保留，重复部分交给下面的按 id 去重收敛。
    const older = current.filter(message =>
      !removed?.has(message.id) && message.timestamp.getTime() <= anchorTime);
    const tailNotifications = current.filter(message =>
      isLocalOnly(message) && message.role === 'notification'
      && message.timestamp.getTime() > anchorTime);

    const byId = new Map<string, Message>();
    [...older, ...tailNotifications, ...validServer].forEach((message, index) => {
      byId.set(message.id || `__local_${index}`, message);
    });
    // Map 保留每个 id 最后一次出现的版本 —— 服务端版本排在最后，因此它胜出
    return Array.from(byId.values()).sort(byTimestamp);
  };

  const currentSessionIdRef = useRef(currentSessionId);
  // useLayoutEffect 保证 ref 在 App 的被动 effect（如 flush wander 消息）之前同步，
  // 避免把 sync 从 App 挪到 Provider 后 effect 顺序变化导致读到陈旧 ref。
  useLayoutEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  // 输入占用态使用 token 而不是单一 boolean。这样语音识别、工具二轮等
  // 生命周期即使跨页面卸载/重挂，也只能由自己的完成回调释放，不会互相误清。
  const beginInputActivity = useCallback((kind: string, label: string) => {
    const token = `${kind}_${Date.now()}_${++activitySeqRef.current}`;
    setInputActivities(current => ({ ...current, [token]: { kind, label } }));
    return token;
  }, []);
  const updateInputActivity = useCallback((token: string, kind: string, label: string) => {
    setInputActivities(current => current[token]
      ? { ...current, [token]: { kind, label } }
      : current);
  }, []);
  const endInputActivity = useCallback((token: string) => {
    setInputActivities(current => {
      if (!current[token]) return current;
      const next = { ...current };
      delete next[token];
      return next;
    });
  }, []);

  const addSession = async () => {
    const privateSessionCount = sessions.length;
    if (privateSessionCount >= 10) {
      alert('最多只能创建10个会话');
      return;
    }

    const newSession: Session = {
      id: Date.now().toString(),
      title: `新会话 ${privateSessionCount + 1}`,
      messages: [],
      createdAt: new Date(),
      updatedAt: new Date()
    };

    try {
      const created = await sessionApi.createSession(newSession.id, newSession.title, newSession.messages.map(msg => ({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp.toISOString()
      })));

      newSession.id = created.id;
      newSession.isPrimary = created.isPrimary || false;
      setSessions(prev => [...prev, newSession]);
      setCurrentSessionId(created.id);
    } catch (error) {
      console.error('创建会话失败:', error);
      alert('创建会话失败，请检查后端服务');
    }
  };

  const deleteSession = async (sessionId: string) => {
    if (sessions.some(session => session.id === sessionId && session.isPrimary)) return;
    if (sessions.length <= 1) {
      alert('至少需要保留一个会话');
      return;
    }

    if (window.confirm('确定要删除这个会话吗？')) {
      try {
        const success = await sessionApi.deleteSession(sessionId);
        if (!success) {
          throw new Error('后端删除失败');
        }

        const newSessions = sessions.filter(session => session.id !== sessionId);
        setSessions(newSessions);

        if (sessionId === currentSessionId) {
          setCurrentSessionId(newSessions[0].id);
          await sessionApi.setCurrentSession(newSessions[0].id);
        }
      } catch (error) {
        console.error('删除会话失败:', error);
        alert('删除会话失败，请检查后端服务');
      }
    }
  };

  const toggleSessionLock = async (sessionId: string) => {
    const result = await sessionApi.toggleSessionLock(sessionId);
    if (result) {
      setSessions(prev =>
        prev.map(s => s.id === sessionId ? { ...s, locked: result.locked } : s)
      );
    } else {
      alert('切换锁定状态失败，请检查后端服务');
    }
  };

  const switchSession = async (sessionId: string) => {
    try {
      const success = await sessionApi.setCurrentSession(sessionId);
      if (!success) {
        throw new Error('设置当前会话失败');
      }

      setCurrentSessionId(sessionId);
    } catch (error) {
      console.error('切换会话失败:', error);
      setCurrentSessionId(sessionId);
    }
  };

  const updateSessionMessages = async (sessionId: string, newMessages: Message[], options: { preserveNotifications?: boolean } = {}) => {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;

    let newTitle = session.title;
    if (session.title.startsWith('新会话') && newMessages.length > 1) {
      const firstUserMessage = newMessages.find(m => m.role === 'user');
      if (firstUserMessage) {
        newTitle = firstUserMessage.content.substring(0, 20) + '...';
      }
    }

    const updatedSession = {
      ...session,
      messages: newMessages,
      updatedAt: new Date(),
      title: newTitle
    };

    try {
      setSessions(prev => prev.map(s => {
        if (s.id !== sessionId) return s;
        const removed = removedMessageIdsRef.current.get(sessionId);
        const merged = options.preserveNotifications ? mergeStreamNotifications(s.messages, newMessages) : newMessages;
        return { ...s, title: newTitle, updatedAt: updatedSession.updatedAt,
          messages: merged.filter(message => !removed?.has(message.id)) };
      }));

      if (newTitle !== session.title) {
        sessionApi.updateSession(sessionId, { title: newTitle })
          .catch(e => console.error('同步标题失败:', e));
      }
    } catch (error) {
      console.error('更新会话失败:', error);
      setSessions(prev => prev.map(s => {
        if (s.id !== sessionId) return s;
        const removed = removedMessageIdsRef.current.get(sessionId);
        const merged = options.preserveNotifications ? mergeStreamNotifications(s.messages, newMessages) : newMessages;
        return { ...s, title: newTitle, updatedAt: updatedSession.updatedAt,
          messages: merged.filter(message => !removed?.has(message.id)) };
      }));
    }
  };

  const upsertSessionMessage = (sessionId: string, message: Message) => {
    const source = sessions.find(session => session.id === sessionId);
    if (!source) return;
    const candidate = upsertOutgoingMessage(source.messages, message);
    const title = source.title.startsWith('新会话') && candidate.length > 1
      ? (candidate.find(item => item.role === 'user')?.content.substring(0, 20) || '') + '...'
      : source.title;
    setSessions(previous => previous.map(session => {
      if (session.id !== sessionId || removedMessageIdsRef.current.get(sessionId)?.has(message.id)) return session;
      return {...session, messages: upsertOutgoingMessage(session.messages, message),
        title: session.title === source.title ? title : session.title, updatedAt: new Date()};
    }));
    if (title !== source.title) {
      void sessionApi.updateSession(sessionId, {title}).catch(error => console.error('同步标题失败:', error));
    }
  };

  const reloadCurrentSession = async (options: { authoritative?: boolean } = {}) => {
    const sid = currentSessionIdRef.current;
    if (!sid) return;
    // 只取尾部窗口。以前这里拿全量，主会话涨到两万多条时单次响应 60MB，
    // 手机 WebView 解析即卡死（见 reconcileServerMessages 第 3 支的说明）。
    const session = await sessionApi.getSession(sid, { limit: MESSAGE_WINDOW });
    if (!session) return;
    setSessions(prev => prev.map(s => {
      if (s.id !== sid) return s;
      const backendMsgs = session.messages.map((apiMsg: any) => mapApiMessageToUi(apiMsg));
      const hasMore = session.hasMore ?? false;
      return {
        ...s,
        messages: reconcileServerMessages(
          sid, s.messages || [], backendMsgs,
          options.authoritative === false || (options.authoritative !== true && isLoadingRef.current),
          hasMore,
        ),
        hasMoreHistory: hasMore,
        tailStartId: s.tailStartId || session.oldestId || undefined,
        tailStartTs: s.tailStartTs || session.oldestTs || undefined,
        tailStartRowId: s.tailStartRowId || session.oldestRowId || undefined,
        updatedAt: new Date(),
      };
    }));
  };

  return (
    <ChatContext.Provider
      value={{
        sessions, setSessions,
        currentSessionId, setCurrentSessionId,
        isLoading, setIsLoading,
        inputActivities, beginInputActivity, updateInputActivity, endInputActivity,
        toolCallStatus, setToolCallStatus,
        streamingTick, setStreamingTick,
        currentSessionIdRef,
        addSession, deleteSession, toggleSessionLock,
        switchSession, updateSessionMessages, upsertSessionMessage, reloadCurrentSession,
        recordRemovedMessages, restoreRemovedMessages, reconcileServerMessages,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error('useChat 必须在 ChatProvider 内使用');
  return ctx;
}
