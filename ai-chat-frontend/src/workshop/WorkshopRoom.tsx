import { useEffect, useState } from 'react';
import { BudgetStatus } from './WorkshopBudget';
import WorkshopPreview from './WorkshopPreview';
import WorkshopQuality from './WorkshopQuality';
import WorkshopFlow from './WorkshopFlow';
import WorkshopConversation from './WorkshopConversation';
import { liveLabel } from './WorkshopLiveStatus';
import { effortLabels } from './WorkshopReasoning';
import type { Artifact, Job, Room } from './types';

export default function WorkshopRoom({ room, fallback, desktop, idleOnly, loading, error, tab, modelLabels, offset, onOffset, onBack, onRetry, preview, onPreview, apiBase, onContribute, backLabel = '返回工单列表' }: {
  desktop?: {status:string;ready:boolean}; idleOnly?: boolean;
  room: Room | null; fallback?: Job; loading: boolean; error: string; tab: 'orders' | 'works';
  labels: Record<string, string>; modelLabels: Record<string, string>; offset: number; onOffset: (offset: number) => void;
  backLabel?: string;
  onBack: () => void; onRetry: () => void; preview: Artifact | null; onPreview: (artifact: Artifact | null) => void; apiBase: string; onContribute: (text: string, requestId: string) => Promise<void>;
}) {
  const job = room?.job || fallback;
  useEffect(() => { document.querySelector('.workshop-panel')?.scrollTo({ top: 0 }); }, [job?.id]);
  return <article className="workshop-room">
    <button type="button" className="workshop-room-back" onClick={onBack}>← {backLabel}</button>
    <h2>{job?.title || '工单详情'}</h2>
    {job && <p>{liveLabel(job,desktop,idleOnly)} · 需求 v{job.revision} · {modelLabels[job.model] || '待开工选定模型'}{job.reasoning_effort ? ` · 最近回合推理 ${effortLabels[job.reasoning_effort] || job.reasoning_effort}` : ''}</p>}
    {loading && <p role="status">正在读取这份工单的完整请求与记录…</p>}
    {error && <div role="alert"><p>{error}</p><button type="button" onClick={onRetry}>重试读取详情</button></div>}
    {room && <>
      <nav className="workshop-room-nav" aria-label="工单详情导航">{[["goal","需求"],["plan","方案"],["chat","对话"],["artifacts","交付"],["inspiration","灵感"]].map(([id,label]) => <a key={id} href={`#workshop-room-${id}`}>{label}</a>)}</nav>
      {room.job.state==='recovery_required' && room.job.reason==='execution_no_delivery' && <ExecutionRetry job={room.job} apiBase={apiBase} onUpdated={onRetry}/>}
      {room.pending_decisions?.map(decision=><section key={decision.id} className="workshop-room-section"><h3>工具手的问题 · 等 AI 回答</h3>{decision.questions.map((question,index)=><div key={index}><p>{question.title}</p>{question.options.length>0 && <ul>{question.options.map(option=><li key={option}>{option}</li>)}</ul>}</div>)}<p>邀请 AI 来访后，他可以逐题回答；已有方案确认保留，答案会进入同一工单。</p></section>)}
      {room.flow && <div className="workshop-room-fold"><WorkshopFlow key={`${room.job.id}:${room.job.revision}`} room={room} flow={room.flow} apiBase={apiBase} onPreview={onPreview} onUpdated={onRetry}/></div>}
      <section id="workshop-room-goal" className="workshop-room-section"><details><summary>AI 的目标与提出缘由 · 查看完整需求</summary><small>当前工单完整请求 · AI 原文</small><pre>{room.job.brief}</pre></details></section>
      <section id="workshop-room-plan" className="workshop-room-section"><h3>当前阶段</h3><p>{liveLabel(room.job,desktop,idleOnly)}</p>{room.job.reason && <p>{room.job.reason==='k_decision_required'?'工具手的补充问题等待 AI 回答，已有方案确认保留':room.job.reason}</p>}<BudgetStatus budget={room.job.budget_summary} /><details><summary>原始用量记录</summary><p>{room.job.tokens.toLocaleString()} / {room.job.token_cap.toLocaleString()} tokens</p></details>
        {room.job.plan ? <details><summary>当前方案 v{room.job.plan_version}</summary><pre>{room.job.plan}</pre></details> : <p>尚未提交方案。讨论完成后，方案会保存在这里供 AI 确认。</p>}
      </section>
      {tab === 'orders' && <section id="workshop-room-chat" className="workshop-room-section"><h3>工单对话</h3><WorkshopConversation messages={room.messages} />
        {offset === 0 && room.total_messages <= 50 && !room.messages.some(message => message.role === 'codex') && <p>已保存 AI 的请求；Codex 尚未返回回复。</p>}
        {room.total_messages > 50 && <div className="workshop-pagination"><button disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset-50))}>上一页</button><span>{offset+1}–{Math.min(offset+50, room.total_messages)} / {room.total_messages}</span><button disabled={offset+50 >= room.total_messages} onClick={() => onOffset(offset+50)}>下一页</button></div>}
      </section>}
      <section id="workshop-room-artifacts" className="workshop-room-section"><h3>阶段交付与作品</h3><div className="workshop-works">{room.artifacts.map(artifact => <article key={artifact.id}><h3>{artifact.name} · 需求 v{artifact.revision}</h3><p>{artifact.summary}</p><details><summary>制作方的检查记录</summary><pre>{artifact.checks}</pre></details><WorkshopQuality quality={artifact.quality} jobId={room.job.id} revision={room.job.revision} artifactId={artifact.id} apiBase={apiBase} allowReview={artifact.revision === room.job.revision} onUpdated={onRetry} /><button onClick={() => onPreview(artifact)}>打开作品</button><a href={`${apiBase}/api/workshop/artifacts/${artifact.id}/download`}>保存文件</a></article>)}</div>
        {room.artifacts.length === 0 && <p>尚无阶段交付。实际文件交付后，会显示制作摘要、检查结果、预览和下载入口。</p>}
        {preview && <WorkshopPreview artifact={preview} apiBase={apiBase} onClose={() => onPreview(null)} />}
      </section>
      <Inspiration key={room.job.id} onContribute={onContribute} />
      <details><summary>真实执行回执 · {room.receipts.length} 条</summary>{room.receipts.map(item => <div key={item.id}><small>{item.created_at} · {item.kind}</small><pre>{JSON.stringify(item.body, null, 2)}</pre></div>)}</details>
    </>}
  </article>;
}


function ExecutionRetry({job,apiBase,onUpdated}:{job:Job;apiBase:string;onUpdated:()=>void}) {
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  return <section className="workshop-room-section"><h3>制作卡点 · 本轮没有新交付</h3><p>方案确认仍保留。请查看工具手最后回复；技术条件修复后，可以核对原回合并继续制作。</p><button type="button" disabled={busy} onClick={async()=>{
    setBusy(true);setNotice('');
    try{const response=await fetch(`${apiBase}/api/workshop/rooms/${job.id}/retry`,{method:'POST',headers:{'Content-Type':'application/json','X-Workshop-UI':'settings'},body:JSON.stringify({revision:job.revision})});const body=await response.json();if(!response.ok)throw new Error(body.detail||'尚未恢复');setNotice('已核对回执并重新排队，仍遵循开工设置。');onUpdated();}catch(cause){setNotice(cause instanceof Error?cause.message:'尚未恢复');}finally{setBusy(false);}
  }}>{busy?'正在核对…':'修复后继续制作'}</button>{notice && <p role="status">{notice}</p>}</section>;
}


const inspirationId = () => globalThis.crypto?.randomUUID?.() || `inspiration-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function Inspiration({ onContribute }: { onContribute: (text: string, requestId: string) => Promise<void> }) {
  const [text, setText] = useState('');
  const [requestId, setRequestId] = useState(inspirationId);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  return <form id="workshop-room-inspiration" className="workshop-inspiration workshop-room-section" onSubmit={async event => {
    event.preventDefault();
    if (busy || !text.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await onContribute(text, requestId);
      setText(''); setRequestId(inspirationId());
      setNotice('已记为人类伙伴的灵感补充，等待讨论；新方案仍由 AI 确认。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '补充未确认保存，可重试。'); }
    finally { setBusy(false); }
  }}>
    <h3>补充灵感或反馈问题 · 再做一版</h3>
    <p>已验收也可以反馈问题、补充点子，再做一版。原话进入同一房间，旧方案与作品留在历史；新方案仍由 AI 确认，预算门槛继续生效。</p>
    <label htmlFor="workshop-inspiration">你的补充</label>
    <textarea id="workshop-inspiration" rows={4} maxLength={30000} value={text} disabled={busy} onChange={event => { setText(event.target.value); setRequestId(inspirationId()); setNotice(''); }} placeholder="例如：输入心情后点击按钮没有反应，希望修复；我还想加一个小小的开灯动画…" />
    <button type="submit" disabled={busy || !text.trim()}>{busy ? '正在保存…' : '送进工单讨论'}</button>
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
  </form>;
}
