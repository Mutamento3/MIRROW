import { useEffect, useState } from 'react';
import type { LongTextCard } from '../types';
import { getApiBase } from '../config';
import './XhsPostCard.css';

function safeXhsUrl(value?: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || url.username || url.password || url.port ||
        !['www.xiaohongshu.com', 'm.xiaohongshu.com', 'xhslink.com'].includes(host)) return null;
    return url.toString();
  } catch { return null; }
}

export default function XhsPostCard({ card, messageId, sessionId }: { card: LongTextCard; messageId?: string; sessionId?: string }) {
  const [open, setOpen] = useState(false);
  const [reported, setReported] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState('');
  useEffect(() => {
    if (!card.comment_draft || !messageId) return;
    let active = true;
    fetch(`${getApiBase()}/api/xhs-posts/comment-report/${encodeURIComponent(messageId)}`)
      .then(response => response.json()).then(data => { if (active) setReported(!!data.self_reported); }).catch(() => {});
    return () => { active = false; };
  }, [card.comment_draft, messageId]);
  const link = safeXhsUrl(card.link);
  const fromK = card.source_status === 'k_read';
  return <>
    <button type="button" className="xhs-post-card" onClick={() => setOpen(true)}>
      <span className="xhs-post-card-stamp">🍠 {fromK ? 'AI 留给人类伙伴的帖子' : '人类伙伴分享的帖子'}</span>
      <strong>{card.title || '一篇小红书帖子'}</strong>
      <span>{card.body ? `${card.body.slice(0, 90)}${card.body.length > 90 ? '…' : ''}` : '轻触查看分享链接'}</span>
      <small>{link ? '查看卡片 · 打开分享链接' : '查看卡片 · 暂不能直达原帖'}</small>
    </button>
    {open && <div className="xhs-post-modal-backdrop" onMouseDown={() => setOpen(false)}>
      <section className="xhs-post-modal" onMouseDown={event => event.stopPropagation()} aria-label="小红书帖子卡片">
        <header><span>🍠 {fromK ? 'AI 的分享' : '人类伙伴的分享'}</span><button type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button></header>
        <h3>{card.title || '一篇小红书帖子'}</h3>
        {card.body && <p>{card.body}</p>}
        {card.comment_draft && <div className="xhs-post-comment-draft"><small>AI 想请人类伙伴帮忙评论 · 草稿供你参考，MIRROW 不会代发</small><p>{card.comment_draft}</p><button type="button" onClick={() => { void navigator.clipboard?.writeText(card.comment_draft || ''); }}>复制评论草稿</button>
          {messageId && sessionId && <button type="button" disabled={reported || reportBusy} onClick={async () => { if (!confirm('你已经在小红书亲自发表评论了吗？MIRROW 只记录你的自述，不会检查或代发平台评论。')) return; setReportBusy(true); setReportError(''); try { const response = await fetch(`${getApiBase()}/api/xhs-posts/comment-report`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId, message_id: messageId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || '记录未完成'); setReported(true); } catch (cause) { setReportError(cause instanceof Error ? cause.message : '记录未完成'); } finally { setReportBusy(false); } }}>{reported ? '✓ 人类伙伴说已经评论' : '我已评论'}</button>}
          {reportError && <small role="alert">{reportError}</small>}
        </div>}
        <small>{fromK ? 'AI 已从专用手机读到可见内容' : '这张卡片只证明人类伙伴分享了链接，AI 尚未因此读到帖子内容'}</small>
        <footer>{link ? <a href={link} target="_blank" rel="noopener noreferrer">打开分享链接 ↗</a> : <span>暂不能直达原帖</span>}</footer>
      </section>
    </div>}
  </>;
}
