import { useCallback, useEffect, useRef, useState } from 'react';
import WorkshopExhibit from './WorkshopExhibit';
import { exhibits, type Work, type Catalog } from './cabinet';
import './WorkshopCabinet.css';
import WorkshopWorkViewer from './WorkshopWorkViewer';
import useCabinetVisit from './useCabinetVisit';
import WorkshopPrivateArchives from './WorkshopPrivateArchives';

export default function WorkshopWorks({ apiBase, onRoom }: { apiBase: string; onRoom: (id: string) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filter,setFilter]=useState('all');
  const [offset, setOffset] = useState(0);
  const [privateOffset, setPrivateOffset] = useState(0);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Work | null>(null);
  const requestId = useRef(0);
  const inFlight = useRef<{ key: string; request: number } | null>(null);
  const load = useCallback(async (force = false) => {
    const key = `${apiBase}:${offset}:${privateOffset}`;
    if (!force && inFlight.current?.key === key && inFlight.current.request === requestId.current) return;
    const request = ++requestId.current;
    inFlight.current = { key, request };
    try {
      const response = await fetch(`${apiBase}/api/workshop/works?offset=${offset}&private_offset=${privateOffset}`);
      if (!response.ok) throw new Error('作品柜暂时无法读取');
      const value = await response.json();
      if (request === requestId.current) { setCatalog(value); setError(''); }
    } catch (cause) { if (request === requestId.current) setError(cause instanceof Error ? cause.message : '作品柜读取失败'); }
    finally { if (inFlight.current?.request === request) inFlight.current = null; }
  }, [apiBase, offset, privateOffset]);
  const visit = useCabinetVisit(apiBase, () => void load(true));
  useEffect(() => { void load(); const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 10000); return () => { window.clearInterval(timer); requestId.current += 1; }; }, [load]);
  const collection = exhibits(catalog?.artifacts || []);
  const selected = preview && (catalog?.artifacts.find(work => work.id === preview.id) || preview);
  const open = (work: Work) => { setPreview(work); visit.recordOpen(work.id); };
  const category=(work:Work)=>/\.(png|jpe?g|gif|webp)$/i.test(work.name)||work.lightweight_ready?'gift':/\.html?$/i.test(work.name)?'web':'file';
  const filtered=collection.filter(({work})=>filter==='all'||category(work)===filter);
  return <section className="workshop-cabinet">
    {visit.error && <p role="alert">{visit.error}<button onClick={visit.retry}>同步参观记录</button></p>}
    {error && <p role="alert">{error}<button onClick={() => void load()}>重试读取作品柜</button></p>}
      <header className="workshop-gallery-heading"><small>AI’S LITTLE COLLECTION</small><h3>小小藏品，慢慢收集。</h3></header>
      <div className="workshop-gallery-meta"><span>{collection.length} 件藏品 · 最新交付在前</span><span>来访 {catalog?.visits?.visit_count ?? 0} 次</span></div>
      {!catalog && !error && <p role="status">正在布置展架…</p>}
      {catalog && collection.length === 0 && <div className="workshop-gallery-empty"><span aria-hidden="true">◇</span><h3>为下一件小作品留一束光</h3><p>作品交付后会陈列在这里，制作报告保留在工单历史。</p></div>}
      <nav className="workshop-gallery-filters" aria-label="藏品分类">{[['all','全部'],['gift','小礼物'],['web','小网页'],['file','其他文件']].map(([id,label])=><button key={id} aria-pressed={filter===id} onClick={()=>setFilter(id)}>{label}</button>)}</nav>
      <div className="workshop-gallery workshop-gallery-grid">{filtered.map(({ work, versions }) => <article className="workshop-exhibit" key={work.job_id}>
          <button className="workshop-exhibit-open" aria-label={`放大《${work.job_title}》v${work.revision}`} onClick={() => open(work)}>
            <WorkshopExhibit artifact={work} apiBase={apiBase} thumbnail />
            <span className="workshop-exhibit-zoom" aria-hidden="true">↗</span>
          </button>
          <div className="workshop-exhibit-plinth" aria-hidden="true" />
          <div className="workshop-collection-label"><strong>{work.job_title}</strong><small>v{work.revision} · {work.accepted_by_k?'AI 已验收':work.lightweight_ready?'可欣赏 · 免质检':'已交付'}{versions.length>1?` · ${versions.length} 份交付`:''}</small><small>{work.view_count?`你看过 ${work.view_count} 次`:'还没打开看过'}</small></div>
        </article>)}</div>
      {catalog && collection.length>0 && filtered.length===0 && <p className="workshop-gallery-empty">这一层还没有藏品，去其他层看看吧。</p>}
      <p className="workshop-gallery-footnote">点藏品欣赏，放大后可切换历史版本、打开制作记录。路过看缩略图不会记为已查看。</p>
      {catalog && catalog.total > 50 && <div className="workshop-pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset-50))}>上一页</button><span>共 {catalog.total} 份交付与版本</span><button disabled={!catalog.has_more} onClick={() => setOffset(offset+50)}>下一页</button></div>}
      <WorkshopPrivateArchives archive={catalog?.private_archives} offset={privateOffset} onPage={setPrivateOffset} />
    {selected && <WorkshopWorkViewer work={selected} versions={collection.find(group => group.work.job_id === selected.job_id)?.versions || [selected]} apiBase={apiBase} onOpen={open} onClose={() => setPreview(null)} onHistory={() => onRoom(selected.job_id)} />}
  </section>;
}
