import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBase } from '../../config';
import { record, type Sources, type SourceKey } from './monitorModel';

const endpoints: Record<SourceKey,string> = { system:'/monitor', tasks:'/api/system/task-status', neurons:'/neurons', mobile:'/api/debug/mobile-liveness', sentinel:'/api/sentinel/status', ambient:'/api/ambient-listening/records?limit=80' };
export const emptySources = (): Sources => Object.fromEntries(Object.keys(endpoints).map(key => [key,{data:null,updatedAt:null,error:null}])) as Sources;

export function useMonitorData() {
  const [sources,setSources] = useState<Sources>(emptySources);
  const [loading,setLoading] = useState(false);
  const [intervalSeconds,setIntervalSeconds] = useState(30);
  const active = useRef<AbortController|null>(null);
  const refresh = useCallback(async () => {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    await Promise.allSettled((Object.entries(endpoints) as [SourceKey,string][]).map(async ([key,path]) => {
      const request = new AbortController();
      const abort = () => request.abort();
      controller.signal.addEventListener('abort',abort,{once:true});
      const timeout = window.setTimeout(abort,20000);
      try {
        const response = await fetch(`${getApiBase()}${path}`,{signal:request.signal,cache:'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload: unknown = await response.json();
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('响应格式不正确');
        const data = record(payload);
        if ((key === 'neurons' && !Array.isArray(data.neurons)) || (key === 'tasks' && !Array.isArray(data.tasks)) || (key === 'system' && typeof data.timestamp !== 'string') || (key === 'mobile' && typeof data.state !== 'string') || (key === 'sentinel' && typeof data.running !== 'boolean') || (key === 'ambient' && (!Array.isArray(data.materials) || !Array.isArray(data.decisions)))) throw new Error('响应缺少观测字段');
        if (!controller.signal.aborted) setSources(previous => ({...previous,[key]:{data,updatedAt:Date.now(),error:null}}));
      } catch (error) {
        if (!controller.signal.aborted) setSources(previous => ({...previous,[key]:{...previous[key],error:error instanceof Error && /^HTTP \d+$/.test(error.message) ? error.message : '请求超时、断连或响应无效'}}));
      } finally {
        window.clearTimeout(timeout);
        controller.signal.removeEventListener('abort',abort);
      }
    }));
    if (active.current === controller) {
      active.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  },[]);
  useEffect(() => {
    void refresh();
    return () => { active.current?.abort(); active.current = null; };
  },[refresh]);
  useEffect(() => {
    if (!intervalSeconds) return;
    const tick = () => { if (document.visibilityState === 'visible') void refresh(); };
    const timer = window.setInterval(tick,intervalSeconds*1000);
    document.addEventListener('visibilitychange',tick);
    return () => { window.clearInterval(timer);document.removeEventListener('visibilitychange',tick); };
  },[intervalSeconds,refresh]);
  return {sources,loading,refresh,intervalSeconds,setIntervalSeconds};
}
