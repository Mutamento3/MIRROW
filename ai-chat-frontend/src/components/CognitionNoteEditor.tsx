import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import { newCognitionDraft, type BookDomain, type CognitionDraft } from './cognitionDraft';

const basePeople: Record<string, string> = { k: 'AI', human: '人类伙伴', shared: '双方', unknown: '暂不分类' };
const hints = { self: '写下 AI 对自己的想法。', other: '写下你想补充的了解或相处偏好。', world: '记下这件事就好。' };

export default function CognitionNoteEditor({ book, name, subject, interest, onDirtyChange, onSaved }: {
  book: BookDomain; name?: string; subject?: string; interest?: string;
  onDirtyChange: (dirty: boolean) => void; onSaved: (entry: CognitionDraft) => void;
}) {
  const [draft, setDraft] = useState<CognitionDraft>(() => newCognitionDraft(book, subject, interest));
  const [original, setOriginal] = useState('');
  const [people, setPeople] = useState(basePeople);
  const [loading, setLoading] = useState(!!name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState(false);
  useEffect(() => {
    let active = true;
    if (name) cognitionRequest(`/books/${book}`).then(data => {
      const found = data.entries.find((entry: CognitionDraft) => entry.name === name);
      if (!found) throw new Error('这条内容已不存在，请返回刷新。');
      if (active) { setDraft(found); setOriginal(found.body); setLoading(false); }
    }).catch(e => { if (active) setError(e.message); });
    cognitionRequest('/other').then(data => { if (active) setPeople({ ...basePeople, ...Object.fromEntries(Object.entries(data.entities).map(([id, item]) => [id, (item as { name: string }).name])) }); }).catch(() => {});
    return () => { active = false; };
  }, [book, name]);
  function change(part: Partial<CognitionDraft>) { setDraft(old => ({ ...old, ...part })); onDirtyChange(true); setPreview(false); }
  async function save() {
    if (saving || loading || !draft.name.trim() || !draft.body.trim()) return;
    setSaving(true); setError('');
    try {
      const result = await cognitionRequest(`/books/${book}/${encodeURIComponent(draft.name.trim())}`, { ...draft, name: draft.name.trim() }, 'PUT');
      onDirtyChange(false); onSaved(result.entry);
    } catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  return <section className="note-editor" aria-label="编辑内容">
    {error && <p role="alert">{error}</p>}
    {loading ? !error && <p role="status">正在读取…</p> : <>
      {book === 'other' && <label className="note-person">关于谁<select aria-label="关于谁" value={draft.subject_id} disabled={saving || !!name} onChange={e => change({ subject_id: e.target.value })}>{Object.entries(people).filter(([id]) => !['k','shared','unknown'].includes(id)).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
      <label>标题<input aria-label="标题" placeholder="起个方便查找的名字" value={draft.name} disabled={saving || !!name} onChange={e => change({ name: e.target.value })} /></label>
      <label>内容<textarea aria-label="内容" placeholder={hints[book]} value={draft.body} disabled={saving} onChange={e => change({ body: e.target.value })} rows={8} /></label>
      <details className="note-options"><summary>其他设置（可不填）</summary>
        {book === 'world' && <label>这件事和谁有关<select value={draft.subject_id} disabled={saving} onChange={e => change({ subject_id: e.target.value })}>{Object.entries(people).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
        {book === 'other' && <>
          <label>记录什么<select value={draft.scope} disabled={saving} onChange={e => change({ scope: e.target.value })}><option value="person">对这个人的了解</option><option value="relationship">彼此的相处</option></select></label>
          <label>怎么知道的<select value={draft.basis} disabled={saving} onChange={e => change({ basis: e.target.value })}><option value="inferred">AI 的理解</option><option value="self_report">对方说过</option><option value="observed">相处中观察到</option><option value="mutual_agreement">双方说定了</option></select></label>
          <label>使用到哪一天<input type="datetime-local" value={draft.expires_at || ''} disabled={saving} onChange={e => change({ expires_at: e.target.value })} /></label>
          <label><input type="checkbox" checked={draft.state !== 'inactive'} disabled={saving} onChange={e => change({ state: e.target.checked ? 'active' : 'inactive' })} />现在仍然适用</label>
        </>}
        <label>内容类型<select value={draft.kind} disabled={saving} onChange={e => change({ kind: e.target.value })}>{Object.entries({ fact: '事实', self_reflection: '自我想法', interpretation: '看法', theory: '概念', agreement: '约定', preference: '偏好' }).filter(([id]) => book === 'other' ? ['interpretation','agreement','preference'].includes(id) : book === 'world' ? !['self_reflection','theory'].includes(id) : true).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        {['self','other'].includes(book) && <label><input type="checkbox" checked={draft.core} disabled={saving} onChange={e => change({ core: e.target.checked })} />每次相关对话都带上这条</label>}
        <label>方便搜索的词<input value={draft.keywords.join('，')} disabled={saving} onChange={e => change({ keywords: e.target.value.split(/[,，]/).map(word => word.trim()).filter(Boolean) })} /></label>
        {book !== 'other' && <label>也可以叫什么<input value={(draft.aliases || []).join('，')} disabled={saving} onChange={e => change({ aliases: e.target.value.split(/[,，]/).map(word => word.trim()).filter(Boolean) })} /></label>}
        {['world'].includes(book) && <label>这是谁的看法<select value={draft.knower_id} disabled={saving} onChange={e => change({ knower_id: e.target.value })}>{Object.entries(people).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
      </details>
      <div className="note-actions"><button className="btn-primary" disabled={saving || !draft.name.trim() || !draft.body.trim()} onClick={save}>{saving ? '保存中…' : '保存'}</button>{!!name && <button disabled={saving} onClick={() => setPreview(!preview)}>{preview ? '收起对比' : '对比修改'}</button>}</div>
      {preview && <div className="cognition-diff"><div><h4>原来</h4><pre>{original}</pre></div><div><h4>现在</h4><pre>{draft.body}</pre></div></div>}
    </>}
  </section>;
}
