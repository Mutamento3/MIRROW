import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import VoiceIdentityEnrollment from './VoiceIdentityEnrollment';

type Entity = { id: string; name: string; type: string };
type Entry = { name: string; body: string; subject_id: string; revision: string; scope: string; basis: string; kind: string; state: string; expires_at: string; core: boolean; keywords: string[]; source?: string };
type Legacy = { id: string; name: string; body: string; active?: boolean; status?: string; expires_at?: string };

export default function OtherBookPanel({ onDirtyChange, focusEntry, onSaved, toolsOnly = false }: { toolsOnly?: boolean; focusEntry?: string; onSaved?: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [entities, setEntities] = useState<Record<string, Entity>>({});
  const [entries, setEntries] = useState<Entry[]>([]);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [bases, setBases] = useState<Record<string, string>>({});
  const [subject, setSubject] = useState('human');
  const [draft, setDraft] = useState<Entry | null>(null);
  const [before, setBefore] = useState('');
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [version, setVersion] = useState(0);
  const [entityName, setEntityName] = useState('');
  const [entityType, setEntityType] = useState('silicon');
  const [legacy, setLegacy] = useState<{ profile: Legacy[]; feedback: Legacy[]; note: string } | null>(null);
  const [context, setContext] = useState<{ core: string; dynamic: string; note: string } | null>(null);
  const [query, setQuery] = useState('');
  const [voiceOpen, setVoiceOpen] = useState(false);
  useEffect(() => {
    let active = true;
    setBusy(true);
    cognitionRequest('/other').then(data => {
      if (active) { setEntities(data.entities); setEntries(data.entries); setScopes(data.scopes); setBases(data.bases); if (focusEntry !== undefined) { const entry = data.entries.find((e: Entry) => e.name === focusEntry); if (entry) { setDraft(entry); setSubject(entry.subject_id); setBefore(entry.body); } else if (!focusEntry) { setDraft({ name: '人类伙伴 · ', body: '', subject_id: 'human', revision: 'new', scope: 'person', basis: 'inferred', kind: 'interpretation', state: 'active', expires_at: '', core: false, keywords: [] }); } } }
    }).catch(e => { if (active) setMessage(e.message); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [version, focusEntry]);
  async function perform(fn: () => Promise<void>) {
    setBusy(true); setMessage('');
    try { await fn(); } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }
  function change(part: Partial<Entry>) { onDirtyChange?.(true); setDraft(old => old ? { ...old, ...part } : null); setPreview(false); }
  return <section aria-label="AI 的他者书">
    {focusEntry === undefined && <><h3>人物管理</h3>
    </>}{busy && <p role="status">处理中…</p>}{message && <p role="status">{message}</p>}
    <label>查看谁<select aria-label="他者对象" disabled={busy || (focusEntry !== undefined && !!focusEntry)} value={subject} onChange={e => { setSubject(e.target.value); if (focusEntry !== undefined) change({ subject_id: e.target.value }); else setDraft(null); setPreview(false); setContext(null); }}>{Object.values(entities).map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
    {focusEntry === undefined && <details onToggle={e => setVoiceOpen(e.currentTarget.open)}><summary>环境声音身份绑定 / 声纹录入</summary>
      {voiceOpen && <VoiceIdentityEnrollment entities={entities} />}
    </details>}
    <details><summary>添加人物</summary>
      <label>名称<input value={entityName} disabled={busy} onChange={e => setEntityName(e.target.value)} /></label>
      <label>类型<select value={entityType} disabled={busy} onChange={e => setEntityType(e.target.value)}><option value="human">人类</option><option value="silicon">硅基实体</option><option value="other">其他实体</option></select></label>
      <button disabled={busy || !entityName.trim()} onClick={() => perform(async () => {
        const e = await cognitionRequest('/other/entities', { name: entityName, type: entityType });
        setEntities(old => ({ ...old, [e.id]: e })); setSubject(e.id); setDraft(null); setContext(null); setEntityName('');
        setMessage('实体已登记。没有连接对方，也没有创建属于对方的认知系统。');
      })}>添加</button>
    </details>
    {!toolsOnly && <div className={focusEntry === undefined ? "cognition-grid" : "focused-editor"}>{focusEntry === undefined && <aside>
      <button disabled={busy || !entities[subject]} onClick={() => { setDraft({ name: `${entities[subject].name} · `, body: '', subject_id: subject, revision: 'new', scope: 'person', basis: 'inferred', kind: 'interpretation', state: 'active', expires_at: '', core: false, keywords: [] }); setBefore(''); setPreview(false); }}>新建认识</button>
      {entries.filter(e => e.subject_id === subject).map(e => <button disabled={busy} key={e.name} onClick={() => { setDraft(e); setBefore(e.body); setPreview(false); }}>{e.name}<small>{scopes[e.scope]} · {bases[e.basis]}{e.core ? ' · 核心' : ''}{e.state === 'inactive' ? ' · 已停用' : ''}</small></button>)}
    </aside>}<section>{draft ? <>
      <label>词条标题<input value={draft.name} disabled={busy || draft.revision !== 'new'} onChange={e => change({ name: e.target.value })} /></label>
      <details className="editor-options"><summary>详细设置 · 依据、有效期与核心</summary><label>范围<select value={draft.scope} disabled={busy} onChange={e => change({ scope: e.target.value })}>{Object.entries(scopes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>认识依据<select value={draft.basis} disabled={busy} onChange={e => change({ basis: e.target.value })}>{Object.entries(bases).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>内容性质<select value={draft.kind} disabled={busy} onChange={e => change({ kind: e.target.value })}><option value="interpretation">认识／认知倾向</option><option value="preference">长期或互动偏好</option><option value="agreement">关系约定</option></select></label>
      <label>关键词<input value={draft.keywords.join(',')} disabled={busy} onChange={e => change({ keywords: e.target.value.split(/[,，]/) })} /></label>
      <label><input type="checkbox" checked={draft.core} disabled={busy} onChange={e => change({ core: e.target.checked })} />作为该对象的核心认识（仅选中该对象的场景常驻）</label>
      <label>状态<select value={draft.state} disabled={busy} onChange={e => change({ state: e.target.value })}><option value="active">启用</option><option value="inactive">停用但保留历史</option></select></label>
      <label>截止时间（可留空，ISO 日期时间）<input placeholder="2026-10-01T00:00:00" value={draft.expires_at || ''} disabled={busy} onChange={e => change({ expires_at: e.target.value })} /></label>
      <small>来源：{draft.source || '人类伙伴手动确认；认识归属于 AI'}</small>
      </details><label>正文<textarea aria-label="他者认识正文" rows={12} value={draft.body} disabled={busy} onChange={e => change({ body: e.target.value })} /></label>
      <button disabled={busy || !draft.name.trim() || !draft.body.trim()} onClick={() => setPreview(true)}>预览保存</button>
      {preview && <><p>对象：{entities[draft.subject_id]?.name}；{scopes[draft.scope]}；{bases[draft.basis]}；{draft.core ? '核心' : '非核心'}；{draft.state === 'active' ? '启用' : '停用'}；截止：{draft.expires_at || '未设置'}</p>
        <div className="cognition-diff"><div><h4>修改前</h4><pre>{before || '新建词条'}</pre></div><div><h4>修改后</h4><pre>{draft.body}</pre></div></div>
        <button disabled={busy} onClick={() => perform(async () => {
          const result = await cognitionRequest(`/books/other/${encodeURIComponent(draft.name)}`, { ...draft, owner_id: 'k', knower_id: 'k' }, 'PUT');
          onDirtyChange?.(false); setDraft(result.entry); setBefore(result.entry.body); setPreview(false); setVersion(n => n + 1); setMessage('他者认识已保存；旧画像和旧反馈未迁移或删除。'); onSaved?.();
        })}>确认保存</button></>}
    </> : <p>选择一条认识，或为当前实体新建。没有记录不表示 AI 不认识对方；旧材料仍保留在兼容视图中。</p>}</section></div>}
    {focusEntry === undefined && <><details><summary>查看对话会带上哪些记录</summary>
      <label>聊什么<input disabled={busy} value={query} onChange={e => setQuery(e.target.value)} /></label>
      {[['current', '仅当前对象'], ['group', '人类伙伴与当前对象'], ['none', '无明确对象']].map(([key, label]) => <button key={key} disabled={busy} onClick={() => perform(async () => setContext(await cognitionRequest('/other/context-preview', { interlocutor_ids: key === 'none' ? [] : key === 'group' ? [...new Set(['human', subject])] : [subject], query } )))}>{label}</button>)}
      {context && <><p>{context.note}</p><h4>核心与关系兼容材料</h4><pre>{context.core || '该对象没有已选中的核心材料'}</pre><h4>本轮相关认识</h4><pre>{context.dynamic || '本轮未选入非核心认识'}</pre></>}
    </details>
    <details><summary>以前的记录</summary>
      <button disabled={busy} onClick={() => perform(async () => setLegacy(await cognitionRequest('/other/legacy-preview')))}>查看记录</button>
      {legacy && <><p>{legacy.note}</p><h4>旧画像：{legacy.profile.length} 项</h4>{legacy.profile.map(r => <article key={r.id}><h4>{r.name}</h4><pre>{r.body}</pre><small>去向尚待确认：具体事实归世界书，跨场景长期认识归他者书。</small></article>)}
        <h4>旧反馈：{legacy.feedback.length} 项</h4>{legacy.feedback.map(r => <article key={r.id}><h4>{r.name} · {r.active ? '当前有效' : '当前未生效'}</h4><pre>{r.body}</pre><small>原状态：{r.status}；截止：{r.expires_at || '未设置'}。拟归 AI—人类伙伴关系认识。</small></article>)}</>}
    </details></>}
  </section>;
}
