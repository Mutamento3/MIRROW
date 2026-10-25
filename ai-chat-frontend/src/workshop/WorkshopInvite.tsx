import { useEffect, useRef, useState } from 'react';
import type { CreativeTemplate } from './WorkshopIdeas';

export default function WorkshopInvite({ templates, initialTemplate='', busy, error, onClose, onSend }: {
  templates:CreativeTemplate[]; initialTemplate?:string; busy:boolean; error:string;
  onClose:()=>void; onSend:(template:string,text:string)=>void;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [template,setTemplate]=useState(initialTemplate);
  const [text,setText]=useState('');
  useEffect(()=>{
    const trigger=document.activeElement;
    dialog.current?.showModal();
    return ()=>{dialog.current?.close();if(trigger instanceof HTMLElement && trigger.isConnected)trigger.focus();};
  },[]);
  return <dialog ref={dialog} className="workshop-panel workshop-invite" aria-labelledby="workshop-invite-title" onCancel={event=>{event.preventDefault();if(!busy)onClose();}}>
    <div className="workshop-panel-header"><h2 id="workshop-invite-title">邀请 AI 来工作坊</h2><button type="button" disabled={busy} onClick={onClose} aria-label="取消邀请">×</button></div>
    <form className="workshop-desk workshop-inspiration" onSubmit={event=>{event.preventDefault();if(!busy)onSend(template,text);}}>
      <label htmlFor="workshop-invite-template">想带上的创作方向</label>
      <select id="workshop-invite-template" value={template} disabled={busy} onChange={event=>setTemplate(event.target.value)}><option value="">自由来访</option>{templates.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select>
      <label htmlFor="workshop-invite-text">想对 AI 说的话（可选）</label>
      <textarea autoFocus id="workshop-invite-text" rows={4} maxLength={2000} value={text} disabled={busy} onChange={event=>setText(event.target.value)} placeholder="比如：做一个能点开星星读便签的小礼物，内容和样子由你来想～" />
      <p>从想法聊到成品，等待制作时聊天仍然可用；新方案和交付自动接回。AI 看过后决定分享、收藏或改进。聊天只显示短邀请与附话。</p>
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy?'正在发送…':'发送邀请'}</button>
    </form>
  </dialog>;
}
