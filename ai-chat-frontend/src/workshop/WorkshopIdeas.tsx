import { useState } from 'react';
import './WorkshopIdeas.css';
export type CreativeTemplate = { id:string; title:string; symbol:string; description:string; output:string; example:string; review_hint?:string };
export default function WorkshopIdeas({ templates, onInvite, busy }: { templates:CreativeTemplate[]; onInvite:(id:string)=>void; busy:boolean }) {
  const [selected, setSelected] = useState('');
  const current = templates.find(item => item.id === selected);
  if (!templates.length) return null;
  return <details className="workshop-ideas">
    <summary><span>✦ 灵感菜单</span><small>一张画，一份小礼物，也可以</small></summary>
    <p>这些是创作起点。AI 来访时也能自行选择或自由创作，不必每次从零做功能。</p>
    <div className="workshop-idea-grid">{templates.map(item => <button key={item.id} type="button" aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}><span aria-hidden="true">{item.symbol}</span><strong>{item.title}</strong><small>{item.description}</small></button>)}</div>
    {current && <section className="workshop-idea-example" aria-live="polite"><h3>{current.title}</h3><p>可以带回：{current.output}</p>{current.review_hint && <p>交付方式：{current.review_hint}。以实际文件是否含交互为准。</p>}<details><summary>看看需求与验收示例</summary><p>{current.example}</p></details><button type="button" disabled={busy} onClick={() => onInvite(current.id)}>{busy ? '邀请处理中…' : '带着这个灵感邀请 AI'}</button><small>邀请发送后落实所选方向；题材和赠言由 AI 填写，交付以真实作品文件为准。</small></section>}
  </details>;
}
