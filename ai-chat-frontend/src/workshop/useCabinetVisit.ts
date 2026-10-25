import { useCallback, useEffect, useRef, useState } from 'react';

/** Entering records a visit; only an explicit open records a work view. */
export default function useCabinetVisit(apiBase: string, onRecorded: () => void) {
  const visitId = useRef(crypto.randomUUID());
  const started = useRef(false);
  const saved = useRef(new Set<string>());
  const opened = useRef(new Set<string>());
  const sending = useRef(false);
  const active = useRef(false);
  const callback = useRef(onRecorded);
  callback.current = onRecorded;
  const [error, setError] = useState('');
  const sync = useCallback(async () => {
    if (sending.current || !active.current || document.hidden) return;
    sending.current = true;
    try {
      do {
        const ids = [...opened.current].filter(id => !saved.current.has(id)).slice(0, 50);
        if (started.current && ids.length === 0) break;
        const response = await fetch(`${apiBase}/api/workshop/works/visits`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop-UI': 'settings' },
          body: JSON.stringify({ request_id: visitId.current, artifact_ids: ids }),
        });
        if (!response.ok) throw new Error('参观记录暂未同步，可重试');
        started.current = true;
        ids.forEach(id => saved.current.add(id));
        if (active.current) { setError(''); callback.current(); }
      } while (active.current && !document.hidden);
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? cause.message : '参观记录暂未同步');
    } finally { sending.current = false; }
  }, [apiBase]);
  useEffect(() => {
    active.current = true;
    const resume = () => { if (!document.hidden) void sync(); };
    document.addEventListener('visibilitychange', resume);
    void sync();
    return () => { active.current = false; document.removeEventListener('visibilitychange', resume); };
  }, [sync]);
  return { error, retry: () => void sync(), recordOpen: (id: string) => { opened.current.add(id); void sync(); } };
}
