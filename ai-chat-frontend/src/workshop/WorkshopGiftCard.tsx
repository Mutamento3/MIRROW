import { useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { getApiBase } from '../config';
import './WorkshopGiftCard.css';
import WorkshopGiftViewer from './WorkshopGiftViewer';

export type GiftCard = { job_id:string; title:string; source?:string; text?:string; artifacts:{id:string; name:string; kind:string; revision:number; summary?:string}[] };
// Android routes an ordinary external link through its native browser intent.
export const browserLinkProps = () => Capacitor.isNativePlatform() ? {} : { target:'_blank', rel:'noopener noreferrer' };

function GiftImage({id,title,apiBase}:{id:string;title:string;apiBase:string}) {
  const [failed,setFailed]=useState(false);
  return failed ? <span className="workshop-gift-unavailable">预览暂不可用，可打开作品重试</span>
    : <img src={`${apiBase}/api/workshop/artifacts/${id}/preview`} alt={title} loading="lazy" onError={()=>setFailed(true)} />;
}

export default function WorkshopGiftCard({card,onOpen,attachment=false,onOpenImage}:{card:GiftCard;onOpen:(id:string)=>void;attachment?:boolean;onOpenImage?:(src:string,filename?:string,alt?:string)=>void}) {
  const apiBase=getApiBase();
  const [opened,setOpened]=useState<GiftCard['artifacts'][number]|null>(null);
  return <article className={`workshop-gift-card${attachment?' workshop-gift-attachment':''}`} aria-label={attachment?'AI 分享的作品':'工作坊交付记录'}>
    {!attachment && <header><small>工作坊交付记录</small><h3>{card.title}</h3></header>}
    {!attachment && card.text && <p className="workshop-gift-note">{card.text}</p>}
    <div className="workshop-gift-items">{card.artifacts.map(item=><div className="workshop-gift-item" key={item.id}>
      <a {...browserLinkProps()} className="workshop-gift-preview" href={`${apiBase}/api/workshop/artifacts/${item.id}/view`} aria-label={`打开 ${item.name}`} onClick={event=>{if(item.kind==='image' && onOpenImage){event.preventDefault();event.stopPropagation();onOpenImage(`${apiBase}/api/workshop/artifacts/${item.id}/preview`,item.name,card.title);}else if(item.kind==='html'||item.kind==='text'){event.preventDefault();event.stopPropagation();setOpened(item);}}}>
        {item.kind==='image'?<GiftImage id={item.id} title={card.title} apiBase={apiBase}/>:<span className="workshop-gift-file"><b aria-hidden="true">{item.kind==='html'?'⌂':'◇'}</b><span>{item.kind==='html'?'轻触进入 · 在 MIRROW 里玩':item.kind==='text'?'打开小笺':'制作资料 · 非作品入口'}</span></span>}
      </a>
      <div className="workshop-gift-caption"><span>{item.kind==='image'?'轻触看大图':card.title}</span><a href={`${apiBase}/api/workshop/artifacts/${item.id}/download`}>保存</a></div>
    </div>)}</div>
    <details className="workshop-gift-details"><summary>关于这份作品</summary><p>{card.title}</p><button onClick={()=>onOpen(card.job_id)}>查看制作历史 / 补充灵感</button></details>
    {opened && <WorkshopGiftViewer item={opened} title={card.title} apiBase={apiBase} onClose={()=>setOpened(null)} onHistory={()=>{setOpened(null);onOpen(card.job_id);}}/>}
  </article>;
}
