import { browserLinkProps } from './WorkshopGiftCard';
import { useEffect, useRef } from 'react';
import WorkshopExhibit from './WorkshopExhibit';
import type { Work } from './cabinet';

export default function WorkshopWorkViewer({ work, versions, apiBase, onOpen, onClose, onHistory }: {
  work: Work; versions: Work[]; apiBase: string; onOpen: (work: Work) => void; onClose: () => void; onHistory: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement;
    const node = dialog.current;
    node?.showModal();
    return () => { node?.close(); if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus(); };
  }, []);
  return <dialog ref={dialog} className="workshop-work-viewer" aria-label="放大作品" onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose(); }}>
    <header><div><small>藏品 · 需求 v{work.revision}</small><h3>{work.job_title}</h3></div><button onClick={onClose} aria-label="关闭放大作品">×</button></header>
    <label className="workshop-viewer-version">版本<select aria-label="藏品版本" value={work.id} onChange={event => { const chosen = versions.find(item => item.id === event.target.value); if (chosen) onOpen(chosen); }}>{versions.map(item => <option value={item.id} key={item.id}>v{item.revision} · {item.name}</option>)}</select></label>
    <WorkshopExhibit key={work.id} artifact={work} apiBase={apiBase} />
    <footer><a {...browserLinkProps()} href={`${apiBase}/api/workshop/artifacts/${work.id}/view`}>浏览器打开 ↗</a><a href={`${apiBase}/api/workshop/artifacts/${work.id}/download`}>保存作品</a><button onClick={onHistory}>制作历史 →</button></footer>
  </dialog>;
}
