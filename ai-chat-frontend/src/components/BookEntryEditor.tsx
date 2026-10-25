import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
export type Domain = 'self' | 'world';
type Entry = { name: string; domain: Domain; body: string; aliases: string[]; keywords: string[]; revision: string; subject_id: string; knower_id: string; kind: string; core: boolean; source?: string; maintenance_links?: { maintenance_item_id: string; source_date: string }[] };
const subjects: Record<string, string> = { k: 'AI', human: '人类伙伴', shared: 'AI 与人类伙伴', unknown: '未标注' };
const kinds: Record<string, string> = { fact: '事实', self_reflection: '自我认识', interpretation: '个人理解', theory: '理论', agreement: '约定', preference: '偏好' };


function emptyEntry(domain: Domain): Entry {
  return { name: '', domain, body: '', aliases: [], keywords: [], revision: 'new', subject_id: domain === 'self' ? 'k' : 'unknown', knower_id: domain === 'self' ? 'k' : 'unknown', kind: domain === 'self' ? 'self_reflection' : 'fact', core: false };
}


export default function BookEntryEditor({ domain, interest = '', onUpdates, onDirtyChange, focusEntry, onSaved }: { domain: Domain; interest?: string; focusEntry?: string; onSaved?: () => void; onUpdates: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState<Entry | null>(null);
  const [before, setBefore] = useState('');
  const [preview, setPreview] = useState(false);
  const [modified, setModified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [person, setPerson] = useState('all');
  const [query, setQuery] = useState('');
  useEffect(() => {
    let active = true;
    setBusy(true);
    cognitionRequest(`/books/${domain}`).then(data => { if (active) { setEntries(data.entries); if (focusEntry !== undefined) { const entry = focusEntry ? data.entries.find((e: Entry) => e.name === focusEntry) : { ...emptyEntry(domain), name: interest ? `${interest}品味` : '', keywords: interest ? ['兴趣爱好', interest] : [] }; setDraft(entry || null); setBefore(entry?.body || ''); } } })
      .catch(e => { if (active) setMessage(e.message); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [domain, focusEntry, interest]);
  const visibleEntries = entries.filter(entry =>
    (person === 'all' || entry.subject_id === person) &&
    (!query || [entry.name, ...entry.keywords].join(' ').includes(query)) &&
    (!interest || [entry.name, ...entry.keywords].join(' ').includes(interest)));
  async function save() {
    if (!draft) return;
    setBusy(true); setMessage('');
    try {
      const data = await cognitionRequest(`/books/${domain}/${encodeURIComponent(draft.name)}`, draft, 'PUT');
      setEntries(previous => [...previous.filter(e => e.name !== data.entry.name), data.entry]);
      setModified(false); onDirtyChange?.(false); setDraft(data.entry); setBefore(data.entry.body); setPreview(false);
      setMessage('已保存并重新加载。旧版本已保留，可用于恢复。'); onSaved?.();
    } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }


  function change(part: Partial<Entry>) { setModified(true); onDirtyChange?.(true); setDraft(old => old ? { ...old, ...part } : null); setPreview(false); }
  function selectEntry(entry: Entry | null) {
    if (modified && !window.confirm('切换条目会放弃尚未保存的编辑，继续？')) return false;
    setModified(false); onDirtyChange?.(false); setDraft(entry); setBefore(entry?.body || ''); setPreview(false);
    return true;
  }
  return <section aria-label={domain === 'self' ? '自我书内容' : '世界书内容'}>
    {busy && <p role="status">读取或保存中…</p>}
    {message && <p role="status">{message}</p>}
    {focusEntry === undefined && <><label>搜索名称与关键词<input value={query} onChange={e => setQuery(e.target.value)} /></label>
    {domain === 'world' && <label>按主体筛选<select value={person} onChange={e => { if (selectEntry(null)) setPerson(e.target.value); }}>{[['all', '全部主体'], ...Object.entries(subjects)].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><small>这是同一份世界书的筛选，不是另一份人物画像；未标注的旧条目仍在“全部主体”中。</small></label>}

        <p>{domain === 'self' ? 'AI 的长期自我认识。核心条目会在各个 Builder 场景完整呈现；一次体验不必成为核心。' : domain === 'world' ? '外部生活事实。标注主体后可按主体筛选查看，个人理解与事实分别保留。' : '共同理论与约定。收尾分析会提取定义候选；修订已有概念需要确认，可在认知更新查看。'}</p>
        </>}
        <div className={focusEntry === undefined ? 'cognition-grid' : 'focused-editor'}>{focusEntry === undefined && <aside>
          <button disabled={busy} onClick={() => selectEntry({ ...emptyEntry(domain), name: interest ? `${interest}品味` : '', keywords: interest ? ['兴趣爱好', interest] : [] })}>新建词条</button>
          {!busy && visibleEntries.length === 0 && <p>暂无匹配条目；可调整筛选，或新建长期认识。</p>}
          {visibleEntries.map(entry => <button key={entry.name} disabled={busy} onClick={() => selectEntry(entry)}>
            {entry.name}{entry.core ? ' · 核心' : ''}<small>{subjects[entry.subject_id]} · {kinds[entry.kind]}</small></button>)}
        </aside>}<section>{draft ? <>
          <label>词条名称<input aria-label="词条名称" value={draft.name} disabled={busy || draft.revision !== 'new'} onChange={e => change({ name: e.target.value })} /></label>
          <details className="editor-options"><summary>详细设置 · 归属、关键词与核心</summary><label>描述主体<select aria-label="描述主体" value={draft.subject_id} disabled={busy || domain === 'self'} onChange={e => change({ subject_id: e.target.value })}>{Object.entries(subjects).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>材料性质<select aria-label="材料性质" value={draft.kind} disabled={busy} onChange={e => change({ kind: e.target.value })}>{Object.entries(kinds).filter(([k]) => domain !== 'world' || !['self_reflection', 'theory'].includes(k)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>观点归属<select aria-label="观点归属" value={draft.knower_id} disabled={busy || domain === 'self'} onChange={e => change({ knower_id: e.target.value })}>{Object.entries(subjects).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {domain === 'self' && <label><input type="checkbox" checked={draft.core} disabled={busy} onChange={e => change({ core: e.target.checked })} />纳入核心自我（各场景常驻）</label>}
          <label>别名（逗号分隔）<input value={draft.aliases.join(',')} disabled={busy} onChange={e => change({ aliases: e.target.value.split(/[,，]/) })} /></label>
          <label>关键词（逗号分隔）<input value={draft.keywords.join(',')} disabled={busy} onChange={e => change({ keywords: e.target.value.split(/[,，]/) })} /></label>
          </details><label>正文<textarea aria-label="认知正文" rows={10} value={draft.body} disabled={busy} onChange={e => change({ body: e.target.value })} /></label>
          <small>来源：{draft.source || '新条目，将记录为人类伙伴手动确认'}</small>
          {focusEntry === undefined && !!draft.maintenance_links?.length && <button disabled={busy} onClick={() => onUpdates()}>查看认知更新依据（{draft.maintenance_links.length} 次更新）</button>}
          {!preview ? <button disabled={busy || !draft.name.trim() || !draft.body.trim()} onClick={() => setPreview(true)}>预览变更</button> : <>
            <div className="cognition-diff"><div><h3>修改前</h3><pre>{before || '新建词条'}</pre></div><div><h3>修改后</h3><pre>{draft.body}</pre></div></div>
            <p>描述：{subjects[draft.subject_id]} · {kinds[draft.kind]} · 观点归属：{subjects[draft.knower_id]}{draft.core ? ' · 纳入核心自我' : ''}</p>
            <button disabled={busy} onClick={save}>确认保存</button></>}
        </> : <p>选择条目查看归属与正文，或新建一条。</p>}</section></div>
  </section>;
}
