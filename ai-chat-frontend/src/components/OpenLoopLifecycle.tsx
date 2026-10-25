import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';

type LoopTransition = {
  action?: string;
  from_status?: string;
  to_status?: string;
  occurred_at?: string;
};

type LoopItem = {
  id: string;
  content: string;
  status: string;
  lifecycle_state: string;
  created_at?: string;
  last_reinforced_at?: string;
  wake_at?: string;
  resolved_at?: string;
  expired_at?: string;
  age_hours?: number | null;
  transitions?: LoopTransition[];
  last_fade_offer?: { offered_at?: string; settled_at?: string; outcome?: string } | null;
};

const STATES: Record<string, string> = {
  foreground: '仍在眼前',
  background: '退到背景',
  snoozed: '暂时放下',
  awaiting_fade: '等待最后浮现',
  fading: '最后浮现中',
  resolved: '已经放下',
  expired: '自然消散',
};

const ACTIONS: Record<string, string> = {
  add: '留在心里',
  hold: '继续记挂',
  reinforce: '重新牵动',
  snooze: '暂时放下',
  wake: '重新浮现',
  resolve: '放下了',
  expire: '自然淡去',
};

function dateLabel(raw?: string): string {
  if (!raw) return '时间未记录';
  const value = new Date(raw);
  return Number.isNaN(value.getTime()) ? raw : value.toLocaleString([], {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export default function OpenLoopLifecycle() {
  const [items, setItems] = useState<LoopItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/affect/open-loops/lifecycle`, { cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '暂时读不到未完心绪。');
      setItems(Array.isArray(body.items) ? body.items : []);
    } catch (err: any) {
      setError(err?.message || '暂时读不到未完心绪。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (loading && !items.length) return <div className="affect-board-loading">正在翻看 AI 尚未放下的心绪…</div>;
  return <section className="open-loop-lifecycle" aria-label="未完心绪的生命周期">
    <div className="open-loop-lifecycle-intro">
      <p>这里是 AI 主动留在心里的未完心绪，不是人类伙伴的心情，也不是一条已经确认的长期记忆。</p>
      <button type="button" onClick={() => void load()} disabled={loading}>{loading ? '更新中…' : '刷新'}</button>
    </div>
    {error && <div className="affect-board-error" role="alert">{error}</div>}
    {!items.length && !error && <div className="affect-empty-state"><span aria-hidden="true">🪶</span><p>眼下没有留下的未完心绪。</p><small>有新的牵挂被 AI 明确保留时，才会出现在这里。</small></div>}
    {items.map(item => <article className="open-loop-lifecycle-card" key={item.id}>
      <div className="open-loop-lifecycle-head"><span className="open-loop-lifecycle-state">{STATES[item.lifecycle_state] || item.lifecycle_state}</span><small>始于 {dateLabel(item.created_at)}</small></div>
      <p className="open-loop-lifecycle-content">{item.content}</p>
      <div className="open-loop-lifecycle-meta">
        <span>上次牵动：{dateLabel(item.last_reinforced_at)}</span>
        {item.lifecycle_state === 'snoozed' && item.wake_at && <span>预定再浮现：{dateLabel(item.wake_at)}</span>}
        {item.lifecycle_state === 'awaiting_fade' && <span>下一次真正进入对话时，可能最后浮现一次；若 AI 未主动强化才会消散。</span>}
        {item.lifecycle_state === 'fading' && <span>这一轮的最后浮现尚未结算。</span>}
        {item.lifecycle_state === 'resolved' && item.resolved_at && <span>放下于 {dateLabel(item.resolved_at)}</span>}
        {item.lifecycle_state === 'expired' && item.expired_at && <span>消散于 {dateLabel(item.expired_at)}</span>}
      </div>
      {!!item.transitions?.length && <details className="open-loop-lifecycle-history"><summary>查看变化轨迹</summary><ol>{item.transitions.map((transition, index) => <li key={`${transition.occurred_at}:${index}`}><span>{ACTIONS[transition.action || ''] || STATES[transition.to_status || ''] || '状态变化'}</span><time>{dateLabel(transition.occurred_at)}</time></li>)}</ol></details>}
    </article>)}
  </section>;
}
