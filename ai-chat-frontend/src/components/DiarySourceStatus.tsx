import { useEffect, useState } from 'react';
import { getApiBase } from '../config';

type Status = {
  generation: { messages: string; body: string };
  retrospective: string;
  current_message_count: number;
  checked_at: string | null;
};

export default function DiarySourceStatus({ date }: { date: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setStatus(null); setFailed(false);
    fetch(`${getApiBase()}/events/diary/${encodeURIComponent(date)}/source-status`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('source-status');
        return response.json() as Promise<Status>;
      }).then(setStatus).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [date]);
  if (failed) return <aside role="status">来源核查暂不可用。</aside>;
  if (!status) return null;
  const complete = status.generation.messages === 'current' && status.generation.body === 'current';
  const changed = Object.values(status.generation).includes('changed');
  return <details className="diary-source-status">
    <summary>{changed ? '生成后来源有变化' : complete ? '生成来源回执一致' : '历史来源回执不完整'}</summary>
    <p>当前可读取 {status.current_message_count} 条材料。
      {!complete && !changed && '无法确认原日记当时使用了全部材料。'}
      {status.retrospective !== 'not_checked' && `事后核查：${status.retrospective === 'unchanged_since_check' ? '自核查以来来源未变' : '核查后日记或来源又有变化'}。正文保留。`}
    </p>
  </details>;
}
