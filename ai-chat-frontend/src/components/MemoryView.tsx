import {useEffect, useRef, useState} from 'react';
import {useMemory, MEMORY_PAGE_SIZE, type MemoryCard} from '../hooks/useMemory';
import TimelineView from './TimelineView';
import MemoryDetail from './MemoryDetail';
import MemoryContinuationStatus from './MemoryContinuationStatus';
import MemoryPager from './MemoryPager';
import './memoryLibrary.css';

const kindLabels: Record<string,string>={thread:'事件链',group:'交流原文窗口',legacy:'旧资料',event:'事件'};

function MemoryView({onBack}: {onBack:()=>void}) {
  const memory=useMemory();
  const [draft,setDraft]=useState('');
  const [selected,setSelected]=useState<MemoryCard|null>(null);
  const [showProgress,setShowProgress]=useState(false);
  const scroll=useRef<HTMLDivElement>(null);
  useEffect(()=>{scroll.current?.scrollTo({top:0});},[memory.offset,memory.memoryTab,memory.query]);
  return <div className="memory-fullscreen">
    <div className="memory-container memory-library">
      <header className="memory-header">
        <div className="memory-header-left">
          <button type="button" className="btn-secondary back-btn" onClick={onBack}>← 返回</button>
          <div><small className="memory-library-eyebrow">MEMORY / CONTINUITY</small><h2>记忆与历史资料</h2></div>
        </div>
        <p>事件、发展脉络与原话，各自有迹可循。</p>
      </header>
      <nav className="memory-tabs" aria-label="记忆分类">
        {([['memories','Memory V2'],['archive','旧资料 · 只读'],['timeline','时间轴']] as const).map(([tab,label])=>
          <button type="button" key={tab} className={`memory-tab ${memory.memoryTab===tab?'active':''}`} onClick={()=>{setSelected(null);memory.changeTab(tab);}}>{label}</button>)}
      </nav>
      {memory.memoryTab==='timeline'?<div className="memory-library-timeline"><TimelineView/></div>:<div className="memory-library-workspace">
        <form className="memory-library-search" onSubmit={event=>{event.preventDefault();memory.search(draft.trim());}}>
          <input aria-label="筛选记忆文字" placeholder="筛选摘要文字，不触发主动深搜" value={draft} onChange={event=>setDraft(event.target.value)}/>
          <button type="submit">筛选</button>
          <button type="button" disabled={memory.memoryLoading} onClick={memory.refresh}>刷新</button>
        </form>
        <div className="memory-library-results" role="status">
          <span>{memory.memoryLoading?'正在读取…':memory.memoryError?'读取未完成':`共 ${memory.total} 条${memory.memoryTab==='memories'?'记忆投影':'旧资料'}`}</span>
          <small>每页 {MEMORY_PAGE_SIZE} 条 · 点卡片查看完整内容</small>
        </div>
        <div className="memory-library-scroll" ref={scroll} aria-label="记忆卡片列表" tabIndex={0} aria-busy={memory.memoryLoading}>
          {memory.memoryTab==='memories'&&<details className="memory-library-progress" onToggle={event=>setShowProgress(event.currentTarget.open)}>
            <summary>日结与事件续接进度 <span>查看状态</span></summary>
            {showProgress&&<MemoryContinuationStatus/>}
          </details>}
          {memory.memoryTab==='archive'&&<p className="memory-library-notice">旧摘要未经 V2 逐条重核；场景链接不代表已确认全部内容覆盖。</p>}
          {memory.partial&&<p role="alert" className="memory-library-notice">部分旧资料无法读取；当前列表不是完整档案。</p>}
          {memory.memoryError?<div className="memory-library-empty" role="alert"><h3>这次没有读到记忆</h3><p>{memory.memoryError}</p><button type="button" onClick={memory.refresh}>重新读取</button></div>
            :memory.memoryLoading?<div className="memory-library-empty" role="status">正在读取这一页，原始记忆不会因翻页改变…</div>
            :memory.total===0?<div className="memory-library-empty"><h3>{memory.query?'没有匹配的摘要':'还没有可展示的条目'}</h3><p>{memory.query?'可以换个词，或清空筛选查看全部。':'未就绪或读取失败会单独显示，不会被当作空记忆。'}</p></div>
            :<div className="memory-library-grid">
              {memory.memories.map(card=><button type="button" key={card.id} className="memory-library-card" onClick={()=>setSelected(card)}>
                <span className="memory-library-card-top"><span className="memory-library-kind" data-kind={card.kind}>{kindLabels[card.kind]||'记忆'}</span><span aria-hidden="true">↗</span></span>
                <strong>{card.name||kindLabels[card.kind]||'记忆片段'}</strong>
                <span className="memory-library-preview">{card.summary||'暂无摘要'}{card.summary_truncated?'…':''}</span>
                <span className="memory-library-card-bottom">
                  <small>{card.date_from||'日期未标注'}{card.date_to&&card.date_to!==card.date_from?` ～ ${card.date_to}`:''}</small>
                  <small>{card.event_count!==undefined?`${card.event_count} 个节点 · `:''}{card.source_count!==undefined?`${card.source_count} 条来源`:'查看详情与来源'}</small>
                </span>
              </button>)}
            </div>}
        </div>
        <MemoryPager offset={memory.offset} total={memory.total} pageSize={MEMORY_PAGE_SIZE} loading={memory.memoryLoading} label="记忆列表" onChange={memory.setOffset}/>
      </div>}
      {selected&&<MemoryDetail key={selected.id} card={selected} onClose={()=>setSelected(null)}/>}
    </div>
  </div>;
}
export default MemoryView;
