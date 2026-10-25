import { useEffect, useRef, useState } from 'react';
import { getApiBase } from '../config';
import './MemoryBoxesPanel.css';

type Source = { timestamp: string; role: string; content: string };
export type MemoryBox = {
  order: number; start_ts: string; end_ts: string; summary: string;
  event_count: number; source_count: number; sources: Source[];
  segment_id?: string; active_date?: string; state?: string;
  stage?: string; kind?: string; preview_truncated?: boolean;
};

function clock(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function MemoryBoxCard({ box }: { box: MemoryBox }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const canLoad = Boolean(box.segment_id && box.active_date);
  const shown = sources ?? box.sources;
  const load = async (append = false) => {
    if (!canLoad || busy.current) return;
    busy.current = true;
    setLoading(true);
    setError('');
    const abort = new AbortController();
    controller.current = abort;
    try {
      const offset = append ? shown.length : 0;
      const response = await fetch(`${getApiBase()}/api/memory-v2/scenes/${box.active_date}/segments/${encodeURIComponent(box.segment_id!)}/sources?offset=${offset}&limit=40`, { signal: abort.signal });
      if (!response.ok) throw new Error(response.status === 404 || response.status === 409 ? '来源已有变化，请重新打开这一天。' : '暂时读不到原文，请重试。');
      const data = await response.json();
      const role = (value: string) => ({ user: '人类伙伴', assistant: 'AI', notification: '通知', system: '系统', event: '事件记录' }[value] ?? value);
      const page: Source[] = data.messages.map((message: Source) => ({ ...message, role: role(message.role) }));
      setSources(append ? [...shown, ...page] : page);
    } catch (reason) {
      if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : '原文读取失败。');
    } finally {
      busy.current = false;
      if (!abort.signal.aborted) setLoading(false);
    }
  };
  const status = box.state === 'open' ? '开放盒 · 原文'
    : box.kind === 'raw' ? '关闭盒 · 摘要待生成或复核'
    : box.stage === 'settled' ? '日结语义片段' : '关闭盒 · 初步印象';
  return (
    <details className="memory-box-card" onToggle={event => {
      if (event.currentTarget.open && sources === null && canLoad) void load();
    }}>
      <summary>
        <span className="memory-box-time">{clock(box.start_ts)}{box.end_ts !== box.start_ts ? `—${clock(box.end_ts)}` : ''}</span>
        <span>{box.summary || '这段原文仍完整保留，尚无可用摘要。'}</span>
        <small>{box.segment_id ? status : `${box.event_count} 个事件`} · {box.source_count} 条来源</small>
      </summary>
      <div className="memory-box-sources">
        {shown.map((source, index) => <div className="memory-box-source" key={`${source.timestamp}-${index}`}>
          <span>{clock(source.timestamp)} · {source.role}</span><p>{source.content}</p>
        </div>)}
        {loading && <p className="memory-boxes-empty">读取完整原文中…</p>}
        {error && <p role="alert" className="memory-boxes-empty">{error} <button type="button" onClick={() => void load(sources !== null)}>重试</button></p>}
        {!loading && !error && canLoad && sources !== null && shown.length < box.source_count && <button type="button" onClick={() => void load(true)}>继续查看原文（{shown.length}/{box.source_count}）</button>}
        {!canLoad && (box.source_count > shown.length || box.preview_truncated) && <p className="memory-boxes-empty">这里是来源预览；完整原文仍在原始对话库中。</p>}
      </div>
    </details>
  );
}

export default function MemoryBoxesPanel({ boxes, loading }: { boxes: MemoryBox[]; loading: boolean }) {
  return <section className="memory-boxes-panel">
    <div className="memory-boxes-heading"><h4>Memory V2 记忆盒</h4><span>{loading ? '读取中…' : `${boxes.length} 个片段`}</span></div>
    {!loading && boxes.length === 0 ? <p className="memory-boxes-empty">这一天还没有可展示的记忆盒。</p>
      : boxes.map(box => <MemoryBoxCard box={box} key={box.segment_id || `${box.order}-${box.start_ts}`} />)}
  </section>;
}
