import { useState, useRef, useEffect } from 'react';
import { getApiBase, getWsBase } from '../config';
import { useChat } from '../context/chat';
import { showToast } from '../components/Toast';
import type { Message, TaskItem, TaskFormData, NotificationCard, MemorialSuggestion } from '../types';
import type { CalendarEventData } from '../Calendar';

const API_BASE = getApiBase();

// 日历 + 定时任务 + 通知卡片 + 纪念日确认（自包含模块，含 2 条私有 WS：task WS + calendar WS）。
// 通知卡片有两个渲染点（面板内 / 顶层悬浮），因此本 hook 必须在 App 调一次、返回值分发到两处，
// 不能面板内部再调一次（会双实例导致通知卡片状态不同步）。
export function useCalendar() {
  const { setSessions, currentSessionIdRef, currentSessionId, reloadCurrentSession } = useChat();

  // ── 定时任务状态 ─────────────────────────────────────────
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [taskTab, setTaskTab] = useState<'regular' | 'checkup'>('regular'); // 类别标签
  const [taskPage, setTaskPage] = useState(0);      // 常规定时任务分页
  const [checkupPage, setCheckupPage] = useState(0); // 查岗分页
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set()); // 折叠状态
  const [newTaskForm, setNewTaskForm] = useState<TaskFormData>({
    title: '',
    description: '',
    reason: '',
    ai_context: '',
    trigger_type: 'datetime',
    trigger_config: {},
    notify_method: ['popup'],
    alarm_enabled: false,
    message_content: '',
    task_type: 'reminder',
  });
  const [showNewTaskForm, setShowNewTaskForm] = useState(false);
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);

  // ── 日历状态 ────────────────────────────────────────────
  const [calendarViewMonth, setCalendarViewMonth] = useState(() => new Date());
  const [eventsByDay, setEventsByDay] = useState<Record<number, CalendarEventData[]>>({});
  const [showEventForm, setShowEventForm] = useState(false);
  const [selectedDateStr, setSelectedDateStr] = useState('');
  const [newEventForm, setNewEventForm] = useState({
    title: '',
    description: '',
    event_type: 'memorial' as 'memorial' | 'period',
    creator: '人类伙伴' as 'AI' | '人类伙伴',
  });

  // 通知卡片
  const [notificationCards, setNotificationCards] = useState<NotificationCard[]>([]);

  // 纪念日确认
  const [pendingMemorial, setPendingMemorial] = useState<MemorialSuggestion | null>(null);
  const calendarWsRef = useRef<WebSocket | null>(null);
  const taskWsRef = useRef<WebSocket | null>(null);
  // 无活跃会话时暂存提醒，等 currentSessionId 出现后注入
  const pendingRemindersRef = useRef<Message[]>([]);

  // ── 定时任务 ────────────────────────────────────────────

  const fetchTasks = async (signal?: AbortSignal) => {
    try {
      const timeoutSignal = AbortSignal.timeout(10000);
      const combinedSignal = signal
        ? AbortSignal.any([signal, timeoutSignal])
        : timeoutSignal;
      const res = await fetch(`${API_BASE}/api/tasks?include_inactive=true`, {
        signal: combinedSignal,
      });
      if (res.ok) {
        const data = await res.json();
        setTasks(data);
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      console.error('获取定时任务失败:', e);
    }
  };

  const createTask = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newTaskForm),
      });
      if (res.ok) {
        await fetchTasks();
        setShowNewTaskForm(false);
        setNewTaskForm({
          title: '',
          description: '',
          reason: '',
          ai_context: '',
          trigger_type: 'datetime',
          trigger_config: {},
          notify_method: ['popup'],
          alarm_enabled: false,
          message_content: '',
          task_type: 'reminder',
        });
      } else {
        const err = await res.json();
        alert(`创建失败: ${err.detail || '未知错误'}`);
      }
    } catch (e) {
      console.error('创建定时任务失败:', e);
      alert('创建定时任务失败');
    }
  };

  const deleteTask = async (taskId: string) => {
    if (!window.confirm('确定要删除此定时任务吗？')) return;
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchTasks();
      }
    } catch (e) {
      console.error('删除定时任务失败:', e);
    }
  };

  const toggleTaskActive = async (task: TaskItem) => {
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${task.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !task.is_active }),
      });
      if (res.ok) {
        await fetchTasks();
      }
    } catch (e) {
      console.error('切换任务状态失败:', e);
    }
  };

  const handleTaskTrigger = (taskData: any) => {
    // 直接将提醒消息添加到当前会话（绕过重载，避免缓存/缓冲问题）
    const sid = currentSessionIdRef.current;

    if (taskData.message_content) {
      const reminderMsg: Message = {
        id: `reminder_${taskData.id || Date.now()}`,
        role: 'assistant',
        content: taskData.message_content,
        timestamp: new Date(),
        isReminder: true,
        thinkingChain: taskData.thinking ? [{
          content: taskData.thinking,
          timestamp: taskData.triggered_at || new Date().toISOString(),
        }] : undefined,
      } as Message;

      if (sid) {
        setSessions(prev => {
          // 去重：防止同一条提醒消息重复添加
          return prev.map(s => {
            if (s.id !== sid) return s;
            const existing = s.messages?.find(m => m.id === reminderMsg.id);
            if (existing) {
              if (existing.thinkingChain?.length || !reminderMsg.thinkingChain?.length) return s;
              return { ...s, messages: s.messages.map(m =>
                m.id === reminderMsg.id ? { ...m, thinkingChain: reminderMsg.thinkingChain } : m
              ) };
            }
            return { ...s, messages: [...(s.messages || []), reminderMsg], updatedAt: new Date() };
          });
        });
      } else {
        // 无活跃会话时缓存提醒，等会话切换后再注入
        pendingRemindersRef.current.push(reminderMsg);
      }
    }

    // 仅 "message" 类型创建通知卡片
    if (taskData.notify_method?.includes('message') && taskData.message_content) {
      const occurrenceId = String(taskData.id || taskData.triggered_at || Date.now());
      const cardId = `card_${occurrenceId}`;
      const seenKey = `mirrow_task_card_seen:${occurrenceId}`;
      try {
        if (localStorage.getItem(seenKey)) return;
        localStorage.setItem(seenKey, String(Date.now()));
      } catch { /* storage unavailable: stable card id still deduplicates this mount */ }
      const newCard: NotificationCard = {
        id: cardId,
        title: taskData.title,
        message: taskData.message_content || taskData.description || '',
        x: 20 + Math.random() * 60,
        y: 100 + Math.random() * 200,
        createdAt: Date.now(),
      };
      setNotificationCards(prev => {
        if (prev.some(c => c.id === newCard.id)) return prev;
        return [...prev, newCard];
      });
    }
  };

  // ── 日历 API ────────────────────────────────────────────

  const fetchCalendarMonth = async (year?: number, month?: number, signal?: AbortSignal) => {
    const d = year && month ? { year, month } : {
      year: calendarViewMonth.getFullYear(),
      month: calendarViewMonth.getMonth() + 1,
    };
    try {
      const timeoutSignal = AbortSignal.timeout(10000);
      const combinedSignal = signal
        ? AbortSignal.any([signal, timeoutSignal])
        : timeoutSignal;
      const res = await fetch(`${API_BASE}/api/calendar/month/${d.year}/${d.month}`, {
        signal: combinedSignal,
      });
      if (res.ok) {
        const data = await res.json();
        const map: Record<number, CalendarEventData[]> = {};
        if (data.days_with_events) {
          for (const [day, events] of Object.entries(data.days_with_events)) {
            map[parseInt(day)] = events as CalendarEventData[];
          }
        }
        setEventsByDay(map);
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      console.error('获取日历数据失败:', e);
    }
  };

  const createCalendarEvent = async () => {
    if (!newEventForm.title.trim() || !selectedDateStr) return;
    try {
      const res = await fetch(`${API_BASE}/api/calendar/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: newEventForm.title,
          description: newEventForm.description,
          event_type: newEventForm.event_type,
          date_string: selectedDateStr,
          creator: newEventForm.creator,
          is_active: true,
        }),
      });
      if (res.ok) {
        await fetchCalendarMonth();
        setShowEventForm(false);
        setNewEventForm({ title: '', description: '', event_type: 'memorial', creator: '人类伙伴' });
      }
    } catch (e) {
      console.error('创建日历事件失败:', e);
    }
  };

  const deleteCalendarEvent = async (eventId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/calendar/events/${eventId}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchCalendarMonth();
      }
    } catch (e) {
      console.error('删除日历事件失败:', e);
    }
  };

  const handleCalendarDateClick = (dateStr: string, isEmpty: boolean) => {
    setSelectedDateStr(dateStr);
    if (isEmpty) {
      // 空白日期 → 弹出创建表单
      setNewEventForm({ title: '', description: '', event_type: 'memorial', creator: '人类伙伴' });
      setShowEventForm(true);
    }
  };

  const handlePeriodDrag = async (range: { start: string; end: string }) => {
    const startDate = new Date(range.start + 'T00:00:00');
    const endDate = new Date(range.end + 'T00:00:00');
    const dates: string[] = [];
    for (let d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
      dates.push(d.toISOString().slice(0, 10));
    }
    try {
      // 跳过已存在生理期的日期（去重）
      let created = 0;
      for (const dateStr of dates) {
        const day = parseInt(dateStr.slice(8, 10), 10);
        const existing = eventsByDay[day] || [];
        if (existing.some(e => e.event_type === 'period')) continue;
        await fetch(`${API_BASE}/api/calendar/events`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: '生理期', description: '',
            event_type: 'period', date_string: dateStr,
            creator: '人类伙伴', is_active: true,
          }),
        });
        created++;
      }
      if (created > 0) await fetchCalendarMonth();
    } catch (e) {
      console.error('创建生理期失败:', e);
    }
  };

  // 右键菜单：单日标记生理期（已存在则删除）
  const handleCreatePeriodDay = async (dateStr: string) => {
    try {
      await fetch(`${API_BASE}/api/calendar/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: '生理期', description: '',
          event_type: 'period', date_string: dateStr,
          creator: '人类伙伴', is_active: true,
        }),
      });
      await fetchCalendarMonth();
    } catch (e) {
      console.error('标记生理期失败:', e);
    }
  };

  const handleCalendarMonthChange = (year: number, month: number) => {
    const d = new Date(year, month - 1, 1);
    setCalendarViewMonth(d);
    fetchCalendarMonth(year, month);
  };

  // 纪念日确认：同意
  const acceptMemorial = async () => {
    if (!pendingMemorial) return;
    try {
      await fetch(`${API_BASE}/api/calendar/events/${pendingMemorial.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: true }),
      });
      setPendingMemorial(null);
      await fetchCalendarMonth();
    } catch (e) {
      console.error('确认纪念日失败:', e);
    }
  };

  // 纪念日确认：拒绝
  const rejectMemorial = async () => {
    if (!pendingMemorial) return;
    try {
      await fetch(`${API_BASE}/api/calendar/events/${pendingMemorial.id}`, { method: 'DELETE' });
      setPendingMemorial(null);
      // 后端会在删除后调 LLM 生成回应（通过 main.py 中的逻辑）
    } catch (e) {
      console.error('拒绝纪念日失败:', e);
    }
  };

  // 移除通知卡片
  const dismissNotificationCard = (cardId: string) => {
    setNotificationCards(prev => prev.filter(c => c.id !== cardId));
  };

  // ── 定时任务推送 WebSocket 持久连接 ──────────────────────
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let disposed = false;

    const connectTaskWs = () => {
      if (taskWsRef.current) {
        taskWsRef.current.onclose = null;
        taskWsRef.current.onerror = null;
        taskWsRef.current.close();
        taskWsRef.current = null;
      }

      const ws = new WebSocket(`${getWsBase()}/api/tasks/ws`);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === 'task_trigger') {
            console.log('[TASK_WS] 收到定时任务触发:', data.task?.title);
            handleTaskTrigger(data.task);
            fetchTasks();
          } else if (data.type === 'task_list_changed') {
            fetchTasks();
          } else if (data.type === 'chat_reload') {
            // 静默刷新会话消息（复查消息推送，无卡片/通知）
            reloadCurrentSession();
          } else if (data.type === 'checkup_alarm_status' && data.status === 'failed') {
            showToast('error', data.message || '手机闹钟设置失败，已改为即时反馈。');
          }
        } catch (e) {
          console.error('任务WS消息解析失败:', e);
        }
      };

      ws.onopen = async () => {
        // 重连后无条件刷新会话（获取断连期间注入的所有消息）
        try { await reloadCurrentSession(); } catch (e) { console.error('[TASK_WS] 重连刷新失败:', e); }
        // 补发断连期间错过的提醒
        try {
          const res = await fetch(`${getApiBase()}/api/tasks/pending`);
          const data = await res.json();
          if (data.success && data.pending) {
            for (const p of data.pending) {
              if (p.original_message) {
                const triggeredAt = Date.parse(p.triggered_at || '');
                // Chat reload is authoritative for old reminders. Rebuild a floating
                // card only for a genuinely recent occurrence missed while offline.
                if (!Number.isFinite(triggeredAt) || Date.now() - triggeredAt > 15 * 60_000) continue;
                handleTaskTrigger({
                  id: p.id,
                  task_id: p.task_id,
                  triggered_at: p.triggered_at,
                  title: p.task_title,
                  message_content: p.original_message,
                  notify_method: ['message'],
                  task_type: p.reminder_type || 'task',
                });
              }
            }
            if (data.count > 0) {
              console.log(`[TASK_WS] 重连后补发 ${data.count} 条未处理提醒`);
            }
          }
        } catch (e) {
          console.error('[TASK_WS] 补发提醒查询失败:', e);
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        reconnectTimer = setTimeout(connectTaskWs, 10000);
      };

      ws.onerror = () => {
        ws.close();
      };

      taskWsRef.current = ws;
    };

    connectTaskWs();

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      if (taskWsRef.current) {
        taskWsRef.current.onclose = null;
        taskWsRef.current.onerror = null;
        if (taskWsRef.current.readyState === WebSocket.OPEN) {
          taskWsRef.current.close();
        }
        taskWsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 日历 WebSocket ──────────────────────────────────────
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let disposed = false;

    const connectCalendarWs = () => {
      if (calendarWsRef.current) { calendarWsRef.current.close(); calendarWsRef.current = null; }
      const ws = new WebSocket(`${getWsBase()}/api/calendar/ws`);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'calendar_events_changed') {
            fetchCalendarMonth();
          } else if (data.type === 'memorial_suggested') {
            setPendingMemorial({ id: data.event.id, title: data.event.title, description: data.event.description, date_string: data.event.date_string });
          } else if (data.type === 'calendar_scan_done') {
            if (data.result?.messages?.length > 0) {
              for (const msg of data.result.messages) {
                if (msg.generated_message) {
                  const newMsg: Message = { id: `cal_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`, role: 'assistant', content: msg.generated_message, timestamp: new Date() };
                  setSessions(prev => {
                    const cid = currentSessionIdRef.current;
                    if (cid && prev.some(s => s.id === cid)) {
                      return prev.map(s => s.id === cid ? { ...s, messages: [...s.messages, newMsg], updatedAt: new Date() } : s);
                    }
                    return prev;
                  });
                }
              }
            }
          }
        } catch (e) { console.error('日历WS解析失败:', e); }
      };
      ws.onclose = () => { if (!disposed) reconnectTimer = setTimeout(connectCalendarWs, 10000); };
      ws.onerror = () => ws.close();
      calendarWsRef.current = ws;
    };

    connectCalendarWs();

    return () => {
      disposed = true;
      clearTimeout(reconnectTimer);
      if (calendarWsRef.current) {
        calendarWsRef.current.onclose = null;
        calendarWsRef.current.onerror = null;
        if (calendarWsRef.current.readyState === WebSocket.OPEN) {
          calendarWsRef.current.close();
        }
        calendarWsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 注入缓存的待处理提醒（之前无活跃会话时暂存的）
  useEffect(() => {
    if (currentSessionId && pendingRemindersRef.current.length > 0) {
      const cached = pendingRemindersRef.current.splice(0);
      setSessions(prev => {
        let updated = prev;
        for (const reminderMsg of cached) {
          if (updated.some(s => s.messages?.some(m => m.id === reminderMsg.id))) continue;
          updated = updated.map(s =>
            s.id === currentSessionId
              ? { ...s, messages: [...(s.messages || []), reminderMsg], updatedAt: new Date() }
              : s
          );
        }
        return updated;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  // 初始加载（原挂在 App 的 loadSessions effect，随 sessionsReload 触发；现改为 hook mount 一次，
  // 之后靠 WS 推送刷新。手动 reload 会话不再连带刷新 tasks/calendar——两者有 WS 实时推送，无影响）
  useEffect(() => {
    fetchTasks();
    fetchCalendarMonth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    taskPanelOpen, setTaskPanelOpen,
    tasks, taskTab, setTaskTab, taskPage, setTaskPage, checkupPage, setCheckupPage,
    expandedTasks, setExpandedTasks, newTaskForm, setNewTaskForm, showNewTaskForm, setShowNewTaskForm,
    calendarViewMonth, eventsByDay, showEventForm, setShowEventForm, selectedDateStr, newEventForm, setNewEventForm,
    notificationCards, pendingMemorial,
    fetchTasks, createTask, deleteTask, toggleTaskActive,
    fetchCalendarMonth, createCalendarEvent, deleteCalendarEvent, handleCalendarDateClick,
    handlePeriodDrag, handleCreatePeriodDay, handleCalendarMonthChange,
    acceptMemorial, rejectMemorial, dismissNotificationCard,
  };
}
