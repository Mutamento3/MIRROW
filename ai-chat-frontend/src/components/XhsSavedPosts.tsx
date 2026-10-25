import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './XhsPostCard.css';

type SavedPost = { post_key: string; node_id: string; title: string; summary: string; source_url: string; last_reason?: string; first_seen_at: string; saved: number };

export default function XhsSavedPosts() {
  const [items, setItems] = useState<SavedPost[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [linkDraft, setLinkDraft] = useState<Record<string, string>>({});
  const [linkConfirmed, setLinkConfirmed] = useState<Record<string, boolean>>({});
  const load = async (q = query) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/xhs-posts/saved?query=${encodeURIComponent(q)}`);
      if (!response.ok) throw new Error('留帖读取失败');
      const data = await response.json();
      setItems(data.items || []);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '留帖读取失败'); }
    finally { setBusy(false); }
  };
  useEffect(() => { void load(''); }, []);
  return <div className="xhs-post-shelf">
    <p>这里是 AI 在 MIRROW 本地留下的帖子，不是小红书平台收藏夹。只有 AI 实际读过并选择留下的帖子会出现。</p>
    <div className="xhs-post-shelf-search"><input value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void load(); }} placeholder="搜标题、可见内容或 AI 的理由" /><button onClick={() => void load()} disabled={busy}>查找</button></div>
    {error && <p role="alert">{error}</p>}
    {busy && <p>正在翻看…</p>}
    {!busy && !items.length && <p>还没有留帖。AI 以后遇见想留下的帖子，会把真实阅读记录放在这里。</p>}
    <div className="xhs-post-shelf-list">{items.map(item => <article key={item.post_key}>
      <small>🍠 AI 留帖 · {item.first_seen_at.slice(0, 16).replace('T', ' ')}</small><h4>{item.title || '无标题帖子'}</h4>
      <p>{item.summary}</p>{item.last_reason && <blockquote>AI 当时想留下：{item.last_reason}</blockquote>}
      <div>{item.source_url ? <a href={item.source_url} target="_blank" rel="noopener noreferrer">打开分享链接 ↗</a> : <span>暂没有可靠的原帖直达链接</span>}
        <button type="button" onClick={async () => { if (!confirm('从 MIRROW 的 AI 留帖中移出？不会操作小红书平台，也不会删除原有漫想记录。')) return; const response = await fetch(`${getApiBase()}/api/xhs-posts/saved/${encodeURIComponent(item.node_id)}/remove`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${item.node_id}` }) }); if (response.ok) void load(); else setError('移出未完成，请稍后重试'); }}>移出留帖</button></div>
      <details className="xhs-post-bind"><summary>{item.source_url ? '改正这篇帖子的链接' : '为这篇帖子补上分享链接'}</summary>
        <p>先从小红书复制分享链接并打开核对；只有你确认它与 AI 当时读到的是同一篇，才会绑定到这条阅读记录。MIRROW 不会按标题猜测。</p>
        <textarea rows={2} value={linkDraft[item.node_id] || ''} onChange={event => setLinkDraft(old => ({ ...old, [item.node_id]: event.target.value }))} placeholder="粘贴分享链接或整段分享文字" />
        <label><input type="checkbox" checked={!!linkConfirmed[item.node_id]} onChange={event => setLinkConfirmed(old => ({ ...old, [item.node_id]: event.target.checked }))} />我已打开链接，确认是同一篇帖子</label>
        <button type="button" disabled={!linkDraft[item.node_id]?.trim() || !linkConfirmed[item.node_id] || busy} onClick={async () => { setBusy(true); setError(''); try { const response = await fetch(`${getApiBase()}/api/xhs-posts/saved/${encodeURIComponent(item.node_id)}/bind-link`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: linkDraft[item.node_id], confirmed_same_post: true }) }); const body = await response.json(); if (!response.ok) throw new Error(body.detail || '链接绑定失败'); setLinkDraft(old => ({ ...old, [item.node_id]: '' })); setLinkConfirmed(old => ({ ...old, [item.node_id]: false })); await load(); } catch (cause) { setError(cause instanceof Error ? cause.message : '链接绑定失败'); } finally { setBusy(false); } }}>确认绑定</button>
      </details>
    </article>)}</div>
  </div>;
}
