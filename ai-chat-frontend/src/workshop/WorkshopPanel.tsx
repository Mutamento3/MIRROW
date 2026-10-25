import { useEffect, useRef, type ReactNode } from 'react';
import { SceneIcon, type WorkshopTab } from './WorkshopScene';

const labels = { orders: '工单与房间', works: '作品柜', settings: '工作坊设置' };

export default function WorkshopPanel({ tab, onTab, onClose, busy, onRefresh, children }: {
  tab: WorkshopTab; onTab: (tab: WorkshopTab) => void; onClose: () => void;
  busy: boolean; onRefresh: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => { dialog?.close(); if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus(); };
  }, []);
  return <dialog ref={ref} className="workshop-panel" aria-labelledby="workshop-panel-title" onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="workshop-panel-inner">
      <div className="workshop-panel-toolbar">
      <header className="workshop-panel-header"><div><small>小机手工作坊</small><h2 id="workshop-panel-title">{labels[tab]}</h2></div>
        <div><button className="workshop-quiet-button" onClick={onRefresh} disabled={busy} aria-label="刷新额度" title="刷新额度"><SceneIcon name="refresh" /></button><button className="workshop-quiet-button" onClick={onClose} aria-label="回到工作坊场景" title="回到场景"><SceneIcon name="close" /></button></div>
      </header>
      <nav className="workshop-panel-nav" aria-label="工作坊页面">{(Object.keys(labels) as WorkshopTab[]).map(value => <button key={value} onClick={() => onTab(value)} aria-pressed={tab === value}><SceneIcon name={value} /><span>{labels[value]}</span></button>)}</nav>
      </div>
      <div className="workshop-desk">{children}</div>
    </div>
  </dialog>;
}
