import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import './CognitionPanel.css';

type Preview = { domain: string; name: string; before: string; after: string; revision: string; suggestion_revision: string; subject_id: string; kind: string; core: boolean; keywords: string[]; aliases: string[]; candidates: { name: string; reason: string }[] };
export default function SuggestionReview({ id, onClose, onApplied }: { id: string; onClose: () => void; onApplied: () => void }) {
  const [domain, setDomain] = useState('world');
  const [name, setName] = useState('');
  const [mode, setMode] = useState('supplement');
  const [entries, setEntries] = useState<{ name: string }[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmedNew, setConfirmedNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setPreview(null); setConfirmedNew(false); setBusy(true);
    cognitionRequest(`/books/${domain}`).then(data => { if (active) setEntries(data.entries); })
      .catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [domain]);
  async function inspect() {
    setBusy(true); setError(''); setPreview(null); setConfirmedNew(false);
    try { setPreview(await cognitionRequest(`/suggestions/${id}/preview`, { domain, name, mode })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function apply() {
    if (!preview) return;
    setBusy(true); setError('');
    try { await cognitionRequest(`/suggestions/${id}/apply`, { ...preview, new_entity_confirmed: confirmedNew }); onApplied(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const needsConfirmation = preview?.domain === 'world' && preview.revision === 'new' && preview.candidates.length > 0;
  return <div className="cognition-overlay" role="dialog" aria-modal="true" aria-label="建议归属与合并"><div className="cognition-panel">
    <header><h2>建议归属与合并</h2><button onClick={onClose}>关闭</button></header>
    <p>先选择归属与目标，再查看完整前后内容。个人认识可转入自我书。</p>
    {error && <p role="alert">{error}</p>}
    <label>保存到<select value={domain} disabled={busy} onChange={e => { setDomain(e.target.value); setName(''); }}><option value="world">世界书</option><option value="self">自我书</option></select></label>
    <label>已有目标<select value={entries.some(e => e.name === name) ? name : ''} disabled={busy} onChange={e => { setName(e.target.value); setPreview(null); }}><option value="">使用建议名称／新建</option>{entries.map(e => <option key={e.name}>{e.name}</option>)}</select></label>
    <label>目标名称（留空沿用建议）<input value={name} disabled={busy} onChange={e => { setName(e.target.value); setPreview(null); }} /></label>
    <label>处理方式<select value={mode} disabled={busy} onChange={e => { setMode(e.target.value); setPreview(null); }}><option value="supplement">补充到已有正文</option><option value="update">更正／替换正文</option></select></label>
    <button disabled={busy} onClick={inspect}>预览归属与差异</button>
    {preview && <>
      <p>目标：{preview.name} · {preview.revision === 'new' ? '新建' : '更新已有词条'}</p>
      {preview.candidates.length > 0 && <article><h3>相关已有词条</h3>{preview.candidates.map(c => <p key={c.name}>{c.name}：{c.reason} <button disabled={busy} onClick={() => { setDomain('world'); setName(c.name); setPreview(null); }}>选为目标</button></p>)}</article>}
      <div className="cognition-diff"><div><h3>修改前</h3><pre>{preview.before || '无已有正文'}</pre></div><div><h3>修改后</h3><pre>{preview.after}</pre></div></div>
      {needsConfirmation && <label><input type="checkbox" checked={confirmedNew} onChange={e => setConfirmedNew(e.target.checked)} />已核对相关词条，这是不同实体，需要新建</label>}
      <button disabled={busy || (!!needsConfirmation && !confirmedNew)} onClick={apply}>确认采纳</button>
    </>}
  </div></div>;
}
