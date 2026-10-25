import { useEffect, useRef } from 'react';
import WorkshopArtifactFrame from './WorkshopArtifactFrame';

export default function WorkshopGiftViewer({ item, title, apiBase, onClose, onHistory }: {
  item:{id:string;name:string;revision:number;summary?:string};title:string;apiBase:string;onClose:()=>void;onHistory:()=>void;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const trigger=document.activeElement;
    dialog.current?.showModal();
    return ()=>{dialog.current?.close();if(trigger instanceof HTMLElement && trigger.isConnected)trigger.focus();};
  },[]);
  return <dialog ref={dialog} className="workshop-gift-viewer" aria-label="欣赏作品" onCancel={event=>{event.preventDefault();event.stopPropagation();onClose();}}>
    <header><h3>{title}</h3><button aria-label="关闭作品" onClick={onClose}>×</button></header>
    <div className="workshop-gift-live"><WorkshopArtifactFrame artifact={{...item,summary:item.summary||'',checks:''}} apiBase={apiBase}/></div>
    <footer><a href={`${apiBase}/api/workshop/artifacts/${item.id}/download`}>保存作品</a><button onClick={onHistory}>制作历史 / 补充灵感</button></footer>
  </dialog>;
}
