import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { getApiBase } from '../config';
import './DayEndIssues.css';

export type DayEndIssue = { id: string; revision: string; date: string; label: string;
  reason: string; detail_reason?: string; resolved: boolean; updated_at: string; locations?: {index:number;source_count:number}[] };

export function DayEndIssuesPanel({ issues, error, busy, onClose, onRefresh, onRemove }: {
  issues: DayEndIssue[]; error: string; busy: string;
  onClose: () => void; onRefresh: () => void; onRemove: (issue: DayEndIssue) => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const buttons = dialog.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
        if (!buttons?.length) return;
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); previous?.focus(); };
  }, [onClose]);
  return <div className="day-end-issues-overlay" onClick={onClose}>
    <div ref={dialog} className="day-end-issues-panel" role="dialog" aria-modal="true"
      aria-labelledby="day-end-issues-title" tabIndex={-1} onClick={e => e.stopPropagation()}>
      <header><h2 id="day-end-issues-title">日总结问题</h2><button onClick={onClose} aria-label="关闭日总结问题">×</button></header>
      <p className="day-end-issues-hint">未解决的问题会持续保留。处理完成后，刷新核对，再移除对应提醒。</p>
      {error && <p className="day-end-issues-error" role="alert">{error}</p>}
      <div className="day-end-issues-list">
        {issues.length === 0 && <p>目前没有待处理的问题。</p>}
        {issues.map(issue => <article key={issue.id}>
          <div><strong>{issue.label}</strong><span className={issue.resolved ? 'resolved' : ''}>{issue.resolved ? '已解决 · 待移除' : '待处理'}</span></div>
          <p>受影响活跃日：<time>{issue.date}</time></p>
          <p>{issue.resolved ? '原失败原因：' : ''}{issue.reason}</p>
          {issue.detail_reason && <p>{issue.detail_reason}</p>}
          {issue.locations?.map(location => <small key={location.index}>候选第 {location.index + 1} 条引用未通过，本次可引用材料 {location.source_count} 条。</small>)}
          <small>失败回执：{issue.updated_at?.slice(0, 19).replace('T', ' ') || '时间未记录'}</small>
          <button disabled={!issue.resolved || !!busy} onClick={() => onRemove(issue)}>
            {busy === issue.id ? '正在核对…' : issue.resolved ? '移除已解决提醒' : '处理成功后可移除'}
          </button>
        </article>)}
      </div>
      <footer><button onClick={onRefresh} disabled={!!busy}>刷新处理状态</button></footer>
    </div>
  </div>;
}

export default function DayEndIssues({ expanded = false }: { expanded?: boolean }) {
  const [issues, setIssues] = useState<DayEndIssue[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const mounted = useRef(true);
  const close = useCallback(() => setOpen(false), []);
  const read = async () => {
    try {
      const response = await fetch(`${getApiBase()}/api/system/day-end-issues`, { cache: 'no-store' });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (mounted.current) { setIssues(data.issues || []); setError(''); }
    } catch {
      if (mounted.current) setError('暂时无法核对后端状态，已有提醒保留；恢复连接后请刷新。');
    }
  };
  useEffect(() => {
    mounted.current = true;
    void read();
    const tick = () => { if (document.visibilityState === 'visible') void read(); };
    const timer = window.setInterval(tick, 15000);
    window.addEventListener('focus', tick);
    return () => { mounted.current = false; window.clearInterval(timer); window.removeEventListener('focus', tick); };
  }, []);
  const remove = async (issue: DayEndIssue) => {
    setBusy(issue.id);
    try {
      const response = await fetch(`${getApiBase()}/api/system/day-end-issues/${issue.id}/acknowledge`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: issue.revision }),
      });
      if (!response.ok) { setError('问题仍未解决或状态已变化，提醒已保留。'); return; }
      await read();
    } catch { setError('未能移除提醒，请恢复连接后重试。'); }
    finally { setBusy(''); }
  };
  return <>
    {issues.length > 0 && <button className={'day-end-issues-trigger' + (expanded ? ' expanded' : '')}
      aria-label={`日总结问题，${issues.filter(i => !i.resolved).length} 项待处理`}
      title="日总结问题" onClick={() => { setOpen(true); void read(); }}>❓</button>}
    {open && createPortal(<DayEndIssuesPanel issues={issues} error={error} busy={busy}
      onClose={close} onRefresh={() => void read()} onRemove={issue => void remove(issue)} />, document.body)}
  </>;
}
