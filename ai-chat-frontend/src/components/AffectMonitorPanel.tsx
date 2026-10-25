import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './AffectMonitorPanel.css';

type Component = {
  component_id: string; label: string; family: string; reason: string;
  effective_intensity?: number; persistence_class?: string; status?: string;
  branch_state?: string; decay_fraction?: number; updated_at?: string;
};
type Checkin = {
  checkin_id: string; revision: number; label: string; emoji: string;
  status: string; note: string; created_at?: string;
};
type Overview = {
  mode: string; settings: Record<string, unknown>;
  projection: {
    version: number; generated_at: string; renderer_self: string; renderer_resonance: string;
    self: { components: Component[] }; resonance: { components: Component[] };
  };
  checkins: Checkin[];
  timer?: { running?: boolean; due_episode_count?: number };
};
type Transition = {
  transition_id: string; operation: string; owner: string; reason: string; created_at?: string;
};

function formatDate(value?: string): string {
  if (!value) return '未返回';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function percent(value?: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '未返回';
}

function flag(value: unknown): string {
  return value === true ? '开启' : value === false ? '关闭' : '未返回';
}

async function read(path: string, signal?: AbortSignal) {
  const response = await fetch(`${getApiBase()}${path}`, { method: 'GET', signal });
  if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`);
  return response.json();
}

function ComponentList({ items, empty }: { items: Component[]; empty: string }) {
  if (!items.length) return <p className="muted">{empty}</p>;
  return <div className="affect-component-list">{items.map(item => (
    <div className="affect-component" key={item.component_id}>
      <div className="affect-component-title"><span>{item.label}</span><span className="muted">{item.persistence_class || '未返回'}</span></div>
      <div className="muted">{item.family} · 当前强度 {percent(item.effective_intensity)} · 已衰减 {percent(item.decay_fraction)}</div>
      {item.reason && <div className="affect-reason">{item.reason}</div>}
      <div className="muted">{item.branch_state || item.status || '未返回'} · {formatDate(item.updated_at)}</div>
    </div>
  ))}</div>;
}

export default function AffectMonitorPanel() {
  const [page, setPage] = useState<'self' | 'resonance'>('self');
  const [snapshot, setSnapshot] = useState<{ overview: Overview; transitions: Transition[] } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const [overview, history] = await Promise.all([
        read('/api/affect/overview', signal),
        read('/api/affect/transitions?limit=30', signal),
      ]);
      // A missing projection is a failed read, not evidence of an empty or disabled engine.
      if (!overview || typeof overview.mode !== 'string' || !overview.settings
          || !Array.isArray(overview.projection?.self?.components)
          || !Array.isArray(overview.projection?.resonance?.components)
          || !Array.isArray(overview.checkins) || !Array.isArray(history?.transitions)) {
        throw new Error('Affect 响应不完整，状态未确认');
      }
      if (signal?.aborted) return;
      setSnapshot({ overview, transitions: history.transitions });
      setError('');
    } catch (err: unknown) {
      if (!signal?.aborted) setError(err instanceof Error ? err.message : '无法读取 Affect');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const overview = snapshot?.overview;
  const projection = overview?.projection;

  return (
    <main className="monitor-page affect-page">
      <div className="monitor-page-header">
        <div><h1>🌤️ 情绪高级监控</h1><p className="muted">只读查看投影、来源履历与人工记录。AI self 来自有效的自述；共振在聊天安静收束后更新。</p></div>
        <button className="btn-secondary" onClick={() => void load()} disabled={loading}>刷新</button>
      </div>
      {loading && <p role="status">正在读取 Affect…</p>}
      {error && <div className="error-banner" role="alert">{error}。{snapshot ? '以下保留上次成功读取的快照。' : '尚未取得可用状态。'}</div>}
      {overview && projection && snapshot && <>
        <p className="muted">快照时间：{formatDate(projection.generated_at)} · 投影 v{projection.version}</p>
        <nav className="affect-tabs" aria-label="Affect 页面">
          <button className={page === 'self' ? 'active' : ''} onClick={() => setPage('self')}>AI 当前</button>
          <button className={page === 'resonance' ? 'active' : ''} onClick={() => setPage('resonance')}>共振与人工记录</button>
        </nav>
        <section className="affect-page-grid">
          {page === 'self' ? <article className="monitor-card affect-primary-card">
            <h2>AI 当前</h2>
            <p className="affect-renderer">{projection.renderer_self || '当前投影没有可注入的 AI self 文本。'}</p>
            <ComponentList items={projection.self.components} empty="当前投影没有 AI self 组件。" />
            <p className="muted">self 注入：{flag(overview.settings.self_context_enabled)} · provisional self：{flag(overview.settings.provisional_self_enabled)}</p>
          </article> : <>
            <article className="monitor-card affect-primary-card">
              <h2>共振</h2>
              <p className="affect-renderer">{projection.renderer_resonance || '当前投影没有可注入的共振文本。'}</p>
              <ComponentList items={projection.resonance.components} empty="当前投影没有共振组件。" />
              <p className="muted">识别人类伙伴状态：{flag(overview.settings.resonance_recognition_enabled)} · 共振注入：{flag(overview.settings.resonance_channel_enabled)}</p>
            </article>
            <article className="monitor-card">
              <h2>人工记录履历</h2>
              <p className="muted">记录与预设管理位于主情绪界面。</p>
              <div className="affect-checkin-list">{overview.checkins.slice(0, 12).map(item => (
                <div className="affect-checkin" key={`${item.checkin_id}:${item.revision}`}>
                  <b>{item.emoji} {item.label}</b><small>{item.status} · {formatDate(item.created_at)}</small>
                  {item.note && <p className="muted">{item.note}</p>}
                </div>
              ))}{!overview.checkins.length && <p className="muted">暂无人工记录。</p>}</div>
            </article>
          </>}
          <article className="monitor-card">
            <h2>最近演变履历</h2>
            <div className="affect-trace-list">{snapshot.transitions.slice(0, 8).map(item => (
              <div key={item.transition_id}><b>{item.operation}</b> · {item.owner} · {item.reason || '未附理由'}<small>{formatDate(item.created_at)}</small></div>
            ))}{!snapshot.transitions.length && <p className="muted">暂无可读履历。</p>}</div>
          </article>
          <article className="monitor-card">
            <h2>系统状态</h2>
            <p>引擎：{overview.mode}</p>
            <p className="muted">安静收束计时器：{flag(overview.timer?.running)}</p>
            <p className="muted">待收束 episode：{overview.timer?.due_episode_count ?? '未返回'} · 安静窗口：{typeof overview.settings.quiet_minutes === 'number' ? `${overview.settings.quiet_minutes} 分钟` : '未返回'}</p>
          </article>
        </section>
      </>}
    </main>
  );
}
