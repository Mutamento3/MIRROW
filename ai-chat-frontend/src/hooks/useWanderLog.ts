import { useState, useRef, useEffect } from 'react';
import { getApiBase } from '../config';

// 漫想日志查看器（纯只读，自包含，不依赖聊天核心态）
export function useWanderLog() {
  const [wanderLogs, setWanderLogs] = useState<Array<any>>([]);
  const [wanderLogFilter, setWanderLogFilter] = useState<'all'|'pushed'|'discarded'>('all');
  const [wanderLogDate, setWanderLogDate] = useState<string>(new Date().toLocaleDateString('en-CA'));
  const [wanderLogStats, setWanderLogStats] = useState<Record<string,any>>({});
  const lastWanderLogViewRef = useRef<string>('');
  const retryTimerRef = useRef<number | null>(null);

  const fetchWanderLogs = async (filter?: string, date?: string) => {
    try {
      const f = filter ?? wanderLogFilter;
      const d = date ?? wanderLogDate;
      const pushedParam = f === 'all' ? '' : `&pushed=${f === 'pushed'}`;
      const dateParam = d ? `&date=${d}` : '';
      const lastViewedParam = lastWanderLogViewRef.current ? `&last_viewed_at=${encodeURIComponent(lastWanderLogViewRef.current)}` : '';
      // 按日期查看时由后端的日期过滤负责边界；limit=0 表示过滤后的全量。
      const res = await fetch(`${getApiBase()}/api/wander/logs?limit=0${dateParam}${pushedParam}${lastViewedParam}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.message === '漫想管理器不可用') throw new Error(data.message);
      setWanderLogs(data.logs || []);
      setWanderLogStats(data.stats || {});
      lastWanderLogViewRef.current = new Date().toISOString();
    } catch (e) {
      console.error('获取漫想日志失败:', e);
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = window.setTimeout(() => void fetchWanderLogs(filter, date), 1500);
    }
  };

  // 面板挂载（打开）时 + 过滤/日期变化时自动刷新
  useEffect(() => {
    fetchWanderLogs();
    return () => {
      if (retryTimerRef.current !== null) window.clearTimeout(retryTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanderLogFilter, wanderLogDate]);

  return { wanderLogs, wanderLogFilter, setWanderLogFilter, wanderLogDate, setWanderLogDate, wanderLogStats, fetchWanderLogs };
}
