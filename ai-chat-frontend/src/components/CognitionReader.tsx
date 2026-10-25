import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import { newCognitionDraft, type CognitionDraft } from './cognitionDraft';
import CognitionMaintenance from './CognitionMaintenance';
import { ReadingBody } from './CognitionReadingBody';
export { ReadingBody } from './CognitionReadingBody';
import { entityLabel, readingTitle, EntityIdentityDetails, type ReadingEntity } from './entityReadingNames';

type Book = 'self' | 'other' | 'world';
type Entry = CognitionDraft & { name: string; body: string; unread?: boolean; category?: string; keywords: string[]; subject_id: string; core?: boolean; source?: string; updated_at?: string; scope?: string; basis?: string; state?: string; expires_at?: string };
const names: Record<string, string> = { k: 'AI', human: '人类伙伴', shared: '双方', unknown: '未标注' };

export default function CognitionReader({ book, interest = '', entryIds, initialEntry = '', onDirtyChange, onEntriesChanged }: { book: Book; interest?: string; entryIds?: string[]; initialEntry?: string; onDirtyChange: (value: boolean) => void; onEntriesChanged?: () => void }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [entities, setEntities] = useState(names);
  const [entityDetails, setEntityDetails] = useState<Record<string,ReadingEntity>>({});
  const [identityError, setIdentityError] = useState(false);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Entry | null>(null);
  const [keywordText, setKeywordText] = useState('');
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [subject, setSubject] = useState('all');
  const [notice, setNotice] = useState('');
  const [history, setHistory] = useState(false);
  const [generation, setGeneration] = useState(0);
  useEffect(()=>{
    if(!selected || editing || loading)return;
    const {name,revision}=selected;
    let active=true;
    cognitionRequest(`/books/${book}/${encodeURIComponent(name)}/read`,{revision},'POST').then(()=>{
      if(active)setEntries(old=>old.map(e=>e.name===name&&e.revision===revision?{...e,unread:false}:e));
      window.dispatchEvent(new Event('cognition-read'));
    }).catch(()=>{});
    return()=>{active=false;};
  },[book,selected?.name,selected?.revision,editing,loading]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    cognitionRequest(`/books/${book}`).then(data => {
      if (!Array.isArray(data.entries)) throw new Error('书籍目录读取未确认，请重试');
      if (active) {
        setEntries(data.entries);
        setSelected(old => data.entries.find((entry: Entry) => entry.name === (old?.name || initialEntry)) || null);
        if (initialEntry && !data.entries.some((entry: Entry) => entry.name === initialEntry)) setNotice('来源书页已改名或不可用，当前显示书籍目录。');
      }
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    setIdentityError(false);
    cognitionRequest('/other').then(data => {
      if (!active) return;
      const details = data.entities as Record<string,ReadingEntity>;
      setEntityDetails(details);
      setEntities({ ...names, ...Object.fromEntries(Object.entries(details).map(([id, entity]) => [id, book === 'other' ? entityLabel(entity) : entity.name])) });
    }).catch(() => {if(active) {setIdentityError(true);setEntityDetails({});setEntities(names);}});
    return () => { active = false; };
  }, [book, generation, initialEntry]);
  const titleOf = (entry:Entry)=>book === 'other' ? readingTitle(entry.name,entityDetails[entry.subject_id]) : entry.name;
  const visible = entries.filter(e => {
    const entity = book === 'other' ? entityDetails[e.subject_id] : undefined;
    const terms = `${e.name} ${e.body} ${e.keywords.join(' ')} ${entity ? [entity.name,entity.preferred_name,...(entity.aliases || [])].filter(Boolean).join(' ') : ''}`;
    return (subject === 'all' || e.subject_id === subject) && (!query || terms.toLocaleLowerCase().includes(query.toLocaleLowerCase())) && (entryIds === undefined || entryIds.includes(String(e.entry_id)));
  });
  const groups = new Map<string, Entry[]>();
  visible.forEach(e => {
    const category = book === 'other' ? e.subject_id : e.category || (e.core ? '核心认识' : '其他条目');
    groups.set(category, [...(groups.get(category) || []), e]);
  });
  // The identity registry can contain a confirmed person before AI has formed
  // any subjective other-book entry. Keep the person visible without writing
  // a fabricated cognition paragraph on AI's behalf.
  const emptyOtherEntities = book === 'other' ? Object.entries(entityDetails).filter(([id, entity]) =>
    id.startsWith('entity_') && !entries.some(entry => entry.subject_id === id) &&
    (subject === 'all' || subject === id) &&
    (!query || [entity.name, entity.preferred_name, ...(entity.aliases || [])]
      .filter((value): value is string => typeof value === 'string' && !!value)
      .some(value => value.toLocaleLowerCase().includes(query.toLocaleLowerCase())))
  ) : [];
  function markDirty(value: boolean) { setDirty(value); onDirtyChange(value); }
  function leaveEdit() {
    if (dirty && !window.confirm('放弃尚未保存的修改？')) return;
    markDirty(false); setEditing(false); setDraft(null);
  }
  async function removeEntry() {
    if (!selected || !['self', 'world'].includes(book) || !window.confirm(`确定删除「${selected.name}」？`)) return;
    try {
      const data = await cognitionRequest(`/books/${book}/${encodeURIComponent(selected.name)}`, {revision:selected.revision}, 'DELETE');
      if (data.status !== 'deleted') throw new Error('删除未确认，请刷新核对');
      setSelected(null); setGeneration(n => n + 1); setNotice('已删除，可在全书更新记录中恢复');
      onEntriesChanged?.();
    } catch (e) { setError((e as Error).message); }
  }
  function startEdit(entry: Entry | null, subjectId?: string) {
    setDraft(entry ? { ...entry } : newCognitionDraft(book, subjectId || (subject === 'all' ? 'human' : subject), interest));
    setKeywordText(entry?.keywords.join('，') || '');
    setEditing(true); setNotice(''); setError('');
  }
  async function saveInline() {
    if (!draft || saving || !draft.name.trim() || !draft.body.trim()) return;
    setSaving(true); setError('');
    try {
      const originalName = selected?.name || draft.name;
      const keywords = keywordText.split(/[,，、\n]/).map(word => word.trim()).filter(Boolean);
      const result = await cognitionRequest(`/books/${book}/${encodeURIComponent(originalName)}`, {...draft, keywords}, 'PUT');
      setSelected(result.entry); setEntries(old => [...old.filter(e => e.name !== originalName && e.name !== result.entry.name), result.entry]);
      setEditing(false); setDraft(null); markDirty(false); setNotice('已保存');
      onEntriesChanged?.();
    } catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  function change(part: Partial<Entry>) { setDraft(old => old ? {...old, ...part} : null); markDirty(true); }
  return <section className={`cognition-reading ${selected || editing ? 'has-selection' : ''}`} aria-label="认知阅读">
    {notice && <p className="reading-notice" role="status">{notice}</p>}<aside className="reading-index" inert={editing ? true : undefined}>
      <div className="reading-search"><input aria-label="搜索条目" placeholder="搜索条目…" value={query} onChange={e => setQuery(e.target.value)} /><button title="新建条目" aria-label="新建条目" onClick={() => { setSelected(null); startEdit(null); }}>＋</button></div>
      {(book === 'world' || book === 'other') && <select aria-label="按主体筛选" value={subject} onChange={e => setSubject(e.target.value)}><option value="all">全部主体</option>{Object.entries(entities).filter(([id]) => book !== 'other' || !['k','shared','unknown'].includes(id)).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>}
      {loading && <p role="status">正在翻开书页…</p>}
      {book === 'other' && identityError && <p role="status">最新人物称呼暂时无法读取，当前显示原词条。<button onClick={()=>setGeneration(n=>n+1)}>重试称呼读取</button></p>}
      {error && <p role="alert">{error}<button onClick={() => setGeneration(n => n + 1)}>重试</button></p>}
      {!loading && !error && visible.length === 0 && emptyOtherEntities.length === 0 && <div className="wb-empty-state"><span className="wb-empty-icon">📖</span><p>{entries.length ? '没有匹配的条目' : '还没有内容'}</p></div>}
      {[...groups].map(([category, items]) => <div className="wb-group" key={category}><h3 className="wb-group-title">{book === 'other' ? entities[category] || category : category}</h3>{items.map(entry => <button className={`wb-entry-item ${selected?.name === entry.name ? 'active' : ''}`} key={entry.name} onClick={() => { setSelected(entry); setHistory(false); setNotice(''); }}><span className="wb-entry-name">{titleOf(entry)}{entry.unread&&<span className="cognition-unread-dot" aria-label="未读更新">●</span>}</span><small>{entry.updated_at ? new Date(entry.updated_at).toLocaleDateString('zh-CN') : entry.core ? '核心认识' : ''}</small></button>)}</div>)}
      {!loading && !error && emptyOtherEntities.map(([id, entity]) => <div className="wb-group" key={id}>
        <h3 className="wb-group-title">{entityLabel(entity)}</h3>
        <button className="wb-entry-item" onClick={() => { setSelected(null); startEdit(null, id); }}>
          <span className="wb-entry-name">身份已登记 · 写下第一条认识</span><small>尚无词条</small>
        </button>
      </div>)}
    </aside>
    <section className="reading-detail">
      {(selected || editing) ? <>
        <button className="reading-mobile-back" onClick={() => { if (editing) leaveEdit(); else setSelected(null); }}>← 返回目录</button>
        <article className="wb-entry-reader">
          <div className="entry-titlebar">
            {editing && draft ? <input aria-label="标题" placeholder="标题" value={draft.name} onChange={e => change({name:e.target.value})} /> : <h2 className="wb-entry-title">{selected && titleOf(selected)}</h2>}
            <button className="entry-edit-toggle" aria-label={editing ? '保存' : '编辑'} title={editing ? '保存' : '编辑'} disabled={saving || (editing && (!draft?.name.trim() || !draft?.body.trim()))} onClick={() => editing ? saveInline() : startEdit(selected)}>{saving ? '…' : editing ? '✓' : '✎'}</button>
          </div>
          {error && <p role="alert">{error}</p>}
          {editing && draft ? <>
            {book === 'other' && draft.revision === 'new' && <label>关于谁<select value={draft.subject_id} onChange={e => change({subject_id:e.target.value})}>{Object.entries(entities).filter(([id]) => !['k','shared','unknown'].includes(id)).map(([id,label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
            <textarea className="entry-inline-text" aria-label="内容" placeholder="写下内容…" value={draft.body} onChange={e => change({body:e.target.value})} />
            <label className="entry-keyword-editor">关键词<input aria-label="关键词" placeholder="用逗号分开" value={keywordText} onChange={e => {setKeywordText(e.target.value); markDirty(true);}} /></label>
            <button className="entry-cancel" disabled={saving} onClick={leaveEdit}>取消</button>
          </> : selected && <>
          <div className="reading-tags">{selected.category && <span>{selected.category}</span>}{selected.core && <span>核心</span>}{book === 'other' && <span>AI 对 {entities[selected.subject_id] || selected.subject_id} 的认识</span>}{selected.state === 'inactive' && <span>已停用</span>}</div>
          <ReadingBody body={selected.body} />
          {book === 'other' && <EntityIdentityDetails entity={entityDetails[selected.subject_id]} originalTitle={selected.name} />}
          <div className="entry-retrieval" aria-label="检索线索"><span>关键词</span><div>{selected.keywords.length ? selected.keywords.map(word => <span className="retrieval-word" key={word}>{word}</span>) : <span>未填写</span>}</div>{!!selected.aliases?.length && <p>别名：{selected.aliases.join('、')}</p>}<small>{book === 'other' ? '对象名与别名参与匹配，正文和关键词也用于语义检索。' : '标题、别名与关键词先匹配；未命中时可通过语义检索补充。'}是否在本轮注入，以监控台实际调用为准。</small></div>
          <div className="reading-actions"><button onClick={() => setHistory(!history)}>{history ? '收起更新记录' : '更新记录'}</button>{['self','world'].includes(book) && <button onClick={removeEntry}>删除条目</button>}</div>
          {history && <CognitionMaintenance domainFilter={book} entryNameFilter={selected.name} entryAliases={selected.aliases} />}</>}
        </article>
      </> : <div className="wb-empty-state"><span className="wb-empty-icon">📖</span><p>选择条目查看</p></div>}
    </section>
  </section>;
}
