import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBase } from '../config';

export interface ConversationCovenantSettings {
  word: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
}

const DEFAULT_SETTINGS: ConversationCovenantSettings = {
  word: '',
  start_time: '00:00',
  end_time: '23:59',
  duration_minutes: 30,
};

const SETTINGS_KEY = 'conversation_covenant';
const LOCAL_SETTINGS_KEY = 'mirrow_conversation_covenant_settings';
const DEADLINE_KEY = 'mirrow_conversation_covenant_deadline';
const SESSION_KEY = 'mirrow_conversation_covenant_session';
const normalizeDurationMinutes = (value: unknown) => {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_SETTINGS.duration_minutes;
  return Math.max(1, Math.min(24 * 60, Math.trunc(parsed)));
};

const isTime = (value: unknown): value is string => (
  typeof value === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(value)
);

const normalizeSettings = (value: unknown): ConversationCovenantSettings => {
  if (!value || typeof value !== 'object') return DEFAULT_SETTINGS;
  const raw = value as Partial<ConversationCovenantSettings>;
  return {
    word: typeof raw.word === 'string' ? raw.word : '',
    start_time: isTime(raw.start_time) ? raw.start_time : DEFAULT_SETTINGS.start_time,
    end_time: isTime(raw.end_time) ? raw.end_time : DEFAULT_SETTINGS.end_time,
    duration_minutes: normalizeDurationMinutes(raw.duration_minutes),
  };
};

const timeToMinutes = (value: string) => {
  const [hours, minutes] = value.split(':').map(Number);
  return hours * 60 + minutes;
};

export const isWithinCovenantWindow = (
  now: Date,
  startTime: string,
  endTime: string,
) => {
  const current = now.getHours() * 60 + now.getMinutes();
  const start = timeToMinutes(startTime);
  const end = timeToMinutes(endTime);
  if (start === end) return true;
  if (start < end) return current >= start && current <= end;
  return current >= start || current <= end;
};

export const formatCovenantCountdown = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
};

export const useConversationCovenant = (
  onBackendEnded: (sessionId: string) => void | Promise<void>,
) => {
  const [settings, setSettings] = useState<ConversationCovenantSettings>(() => {
    try {
      return normalizeSettings(JSON.parse(localStorage.getItem(LOCAL_SETTINGS_KEY) || 'null'));
    } catch {
      return DEFAULT_SETTINGS;
    }
  });
  const [deadline, setDeadline] = useState<number | null>(() => {
    const saved = Number(localStorage.getItem(DEADLINE_KEY));
    const sessionId = localStorage.getItem(SESSION_KEY);
    return Number.isFinite(saved) && saved > Date.now() && sessionId ? saved : null;
  });
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() => (
    Number(localStorage.getItem(DEADLINE_KEY)) > Date.now()
      ? localStorage.getItem(SESSION_KEY)
      : null
  ));
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(() => (
    deadline === null ? null : Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
  ));
  const [activationNotice, setActivationNotice] = useState(false);
  const [cancelPromptOpen, setCancelPromptOpen] = useState(false);
  const onBackendEndedRef = useRef(onBackendEnded);
  const noticeTimerRef = useRef<number | null>(null);
  const expiringRef = useRef(false);
  const backendStateReadyRef = useRef(false);
  const lastBackendEndRef = useRef<string | null>(null);

  useEffect(() => { onBackendEndedRef.current = onBackendEnded; }, [onBackendEnded]);

  useEffect(() => {
    try {
      const saved = Number(localStorage.getItem(DEADLINE_KEY));
      if (saved && saved <= Date.now()) {
        localStorage.removeItem(DEADLINE_KEY);
        localStorage.removeItem(SESSION_KEY);
      }
    } catch {
      // localStorage 不可用时仍可继续使用本次页面内状态。
    }

    fetch(`${getApiBase()}/api/settings`)
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(data => {
        const loaded = normalizeSettings(data?.[SETTINGS_KEY]);
        setSettings(loaded);
        localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(loaded));
      })
      .catch(() => {});

    const syncBackendState = async () => {
      try {
        const response = await fetch(`${getApiBase()}/api/conversation-covenant/status`);
        if (!response.ok) return;
        const data = await response.json();
        const backendDeadline = Number(data?.deadline_epoch) * 1000;
        const backendSession = typeof data?.session_id === 'string' ? data.session_id : '';
        if (data?.active && Number.isFinite(backendDeadline) && backendDeadline > Date.now() && backendSession) {
          setDeadline(backendDeadline);
          setActiveSessionId(backendSession);
          localStorage.setItem(DEADLINE_KEY, String(backendDeadline));
          localStorage.setItem(SESSION_KEY, backendSession);
        }

        const lastEndAt = typeof data?.last_end_at === 'string' ? data.last_end_at : null;
        const lastEndSession = typeof data?.last_end_session_id === 'string' ? data.last_end_session_id : '';
        if (!backendStateReadyRef.current) {
          lastBackendEndRef.current = lastEndAt;
          backendStateReadyRef.current = true;
        } else if (lastEndAt && lastEndAt !== lastBackendEndRef.current) {
          lastBackendEndRef.current = lastEndAt;
          setDeadline(null);
          setActiveSessionId(null);
          setRemainingSeconds(null);
          localStorage.removeItem(DEADLINE_KEY);
          localStorage.removeItem(SESSION_KEY);
          if (lastEndSession) void onBackendEndedRef.current(lastEndSession);
        }
      } catch {
        // 后端不可达时保留原有本地倒计时兜底。
      }
    };
    void syncBackendState();
    const backendPoll = window.setInterval(syncBackendState, 2000);

    return () => {
      if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
      window.clearInterval(backendPoll);
    };
  }, []);

  useEffect(() => {
    if (deadline === null) {
      expiringRef.current = false;
      return;
    }

    const tick = () => {
      const next = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemainingSeconds(next);
      if (next > 0 || expiringRef.current || !activeSessionId) return;

      expiringRef.current = true;
      setDeadline(null);
      setActiveSessionId(null);
      localStorage.removeItem(DEADLINE_KEY);
      localStorage.removeItem(SESSION_KEY);
      setCancelPromptOpen(false);
      // 本地计时器只负责收起显示。话题结束是后端倒计时的唯一职责；
      // 后端完成后，上方状态轮询会触发 SQLite 权威消息同步。
    };

    tick();
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, [activeSessionId, deadline]);

  const saveSettings = useCallback(async (next: ConversationCovenantSettings) => {
    const normalized = normalizeSettings(next);
    setSettings(normalized);
    localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(normalized));
    if (!normalized.word.trim()) {
      setDeadline(null);
      setActiveSessionId(null);
      setRemainingSeconds(null);
      setCancelPromptOpen(false);
      localStorage.removeItem(DEADLINE_KEY);
      localStorage.removeItem(SESSION_KEY);
      void fetch(`${getApiBase()}/api/conversation-covenant/cancel`, {
        method: 'POST',
      }).catch(() => {});
    }
    try {
      const response = await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: SETTINGS_KEY, value: normalized }),
      });
      return response.ok;
    } catch {
      return false;
    }
  }, []);

  const tryActivate = useCallback((message: string, sessionId: string) => {
    const word = settings.word.trim();
    if (deadline !== null || !word || !message.includes(word)) return false;
    if (!isWithinCovenantWindow(new Date(), settings.start_time, settings.end_time)) return false;

    const durationSeconds = normalizeDurationMinutes(settings.duration_minutes) * 60;
    const nextDeadline = Date.now() + durationSeconds * 1000;
    setDeadline(nextDeadline);
    setActiveSessionId(sessionId);
    setRemainingSeconds(durationSeconds);
    localStorage.setItem(DEADLINE_KEY, String(nextDeadline));
    localStorage.setItem(SESSION_KEY, sessionId);
    setActivationNotice(true);
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setActivationNotice(false), 3000);
    void fetch(`${getApiBase()}/api/conversation-covenant/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    }).catch(() => {});
    return true;
  }, [deadline, settings]);

  const cancelCountdown = useCallback(() => {
    setDeadline(null);
    setActiveSessionId(null);
    setRemainingSeconds(null);
    setCancelPromptOpen(false);
    localStorage.removeItem(DEADLINE_KEY);
    localStorage.removeItem(SESSION_KEY);
    void fetch(`${getApiBase()}/api/conversation-covenant/cancel`, {
      method: 'POST',
    }).catch(() => {});
  }, []);

  return {
    settings,
    saveSettings,
    tryActivate,
    remainingSeconds,
    formattedCountdown: deadline === null || remainingSeconds === null
      ? null
      : formatCovenantCountdown(remainingSeconds),
    activationNotice,
    cancelPromptOpen,
    openCancelPrompt: () => setCancelPromptOpen(true),
    closeCancelPrompt: () => setCancelPromptOpen(false),
    cancelCountdown,
  };
};
