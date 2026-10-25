import {useEffect, useRef, useState} from 'react';
import {readMemory, type MemoryCard} from '../hooks/useMemory';
import MemoryPager from './MemoryPager';

interface Detail {
  nodes?: {id:string;summary:string;occurred_at:string;reported_at:string;epistemic_status:string;summary_truncated?:boolean}[];
  node_total?:number;summaries?:string[];content?:string;content_truncated?:boolean;
  source_state:string;source_count?:number;
}
interface Sources {
  messages:{message_id:string;timestamp:string;role:string;content:string;content_truncated:boolean;span_selected:boolean}[];
  total:number;truncated:boolean;omitted_in_page:number;
}

export default function MemoryDetail({card,onClose}:{card:MemoryCard;onClose:()=>void}) {
  const [detail,setDetail]=useState<Detail|null>(null);
  const [sources,setSources]=useState<Sources|null>(null);
  const [detailError,setDetailError]=useState(''),[sourceError,setSourceError]=useState('');
  const [detailLoading,setDetailLoading]=useState(true),[sourceLoading,setSourceLoading]=useState(false);
  const [offset,setOffset]=useState(0),[sourceOffset,setSourceOffset]=useState(0);
  const [nodeTotal,setNodeTotal]=useState(0),[sourceTotal,setSourceTotal]=useState(0);
  const [showSources,setShowSources]=useState(false);
  const [revision,setRevision]=useState(0),[sourceRevision,setSourceRevision]=useState(0);
  const dialog=useRef<HTMLDialogElement>(null),body=useRef<HTMLDivElement>(null);
  const legacy=card.kind==='legacy';
  const endpoint=`${legacy?'archive':'memories'}/${encodeURIComponent(card.id)}`;
  useEffect(()=>{
    const focused=document.activeElement as HTMLElement|null;
    const panel=dialog.current;
    panel?.showModal();
    return()=>{panel?.close();if(focused?.isConnected)focused.focus({preventScroll:true});};
  },[]);
  useEffect(()=>{body.current?.scrollTo({top:0});},[offset,sourceOffset,showSources]);
  useEffect(()=>{
    const controller=new AbortController();
    setDetailError('');setDetailLoading(true);
    readMemory<Detail>(`${endpoint}?offset=${offset}&limit=20`,controller.signal)
      .then(data=>{if(!controller.signal.aborted){setDetail(data);setNodeTotal(data.node_total||0);}})
      .catch(error=>{if(!controller.signal.aborted)setDetailError(error.message);})
      .finally(()=>{if(!controller.signal.aborted)setDetailLoading(false);});
    return()=>controller.abort();
  },[endpoint,offset,revision]);
  useEffect(()=>{
    if(!showSources||legacy)return;
    const controller=new AbortController();
    setSourceError('');setSourceLoading(true);
    readMemory<Sources>(`${endpoint}/sources?offset=${sourceOffset}&limit=5`,controller.signal)
      .then(data=>{if(!controller.signal.aborted){setSources(data);setSourceTotal(data.total);}})
      .catch(error=>{if(!controller.signal.aborted)setSourceError(error.message);})
      .finally(()=>{if(!controller.signal.aborted)setSourceLoading(false);});
    return()=>controller.abort();
  },[endpoint,sourceOffset,showSources,legacy,sourceRevision]);
  const loading=showSources?sourceLoading:detailLoading;
  const error=showSources?sourceError:detailError;
  return <dialog ref={dialog} className="memory-detail-panel" aria-label="记忆详情" onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===event.currentTarget)onClose();}}>
    <header><div><small className="memory-library-eyebrow">{legacy?'旧资料 · 只读':'MEMORY V2 / 记忆详情'}</small><h3>{card.name||'记忆片段'}</h3></div><button type="button" autoFocus onClick={onClose}>关闭</button></header>
    {!legacy&&<div className="memory-detail-tabs" role="tablist" aria-label="详情内容">
      <button type="button" role="tab" aria-selected={!showSources} onClick={()=>setShowSources(false)}>事件与脉络</button>
      <button type="button" role="tab" aria-selected={showSources} onClick={()=>setShowSources(true)}>精确来源原文</button>
    </div>}
    <div className="memory-detail-body" ref={body} aria-busy={loading}>
      {error?<div className="memory-library-empty" role="alert"><p>{error}</p><button type="button" onClick={()=>showSources?setSourceRevision(value=>value+1):setRevision(value=>value+1)}>重新读取</button></div>
        :loading?<p className="memory-library-empty" role="status">{showSources?'正在读取原文…':'正在核对来源…'}</p>
        :showSources&&sources?<>
          <p className="memory-library-notice">共 {sources.total} 条来源消息；带跨度锚点时展示对应的原话摘录。</p>
          {sources.messages.map(message=><article key={message.message_id}><small>{message.timestamp.replace('T',' ').slice(0,16)} · {({user:'人类伙伴',assistant:'AI',event:'事件记录',system:'系统',notification:'通知'} as Record<string,string>)[message.role]||message.role}{message.span_selected?' · 原话摘录':''}</small><p className="memory-detail-text">{message.content}{message.content_truncated?'（单条原文已截取）':''}</p></article>)}
          {sources.omitted_in_page>0&&<p className="memory-library-notice">本页有 {sources.omitted_in_page} 条因展示预算省略。</p>}
          {sources.total===0&&<p className="memory-library-empty">没有可展示的来源原文。</p>}
        </>:detail?<>
          <p className="memory-library-notice">{legacy?'旧摘要，来源链接未经逐消息核验。':'本次读取已核对当前原文版本；按事件发生／报告时间展示。'}</p>
          <p className="memory-library-notice">资料来源：{card.source}</p>
          {detail.content&&<p className="memory-detail-text">{detail.content}{detail.content_truncated?'（已达展示长度上限）':''}</p>}
          {detail.nodes?.map(node=><article key={node.id}><small>{node.occurred_at||node.reported_at} · {node.epistemic_status==='model_inference'?'当时的推断':'有来源的事件记录'}</small><p className="memory-detail-text">{node.summary}{node.summary_truncated?'（摘要展示已截取）':''}</p></article>)}
          {detail.summaries?.map((text,index)=><p className="memory-detail-text" key={index}>{text}</p>)}
        </>:null}
    </div>
    {(showSources||nodeTotal>20)&&<MemoryPager offset={showSources?sourceOffset:offset} total={showSources?sourceTotal:nodeTotal} pageSize={showSources?5:20} loading={loading} label={showSources?'来源原文':'事件节点'} onChange={showSources?setSourceOffset:setOffset}/>}
  </dialog>;
}
