import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';

type Entity = { id:string; name:string; aliases?:string[]; preferred_name?:string; revision:string };
export default function EntityNamesEditor({onDirtyChange}:{onDirtyChange:(dirty:boolean)=>void}) {
  const [entities,setEntities] = useState<Record<string,Entity>>({});
  const [id,setId] = useState(''), [aliases,setAliases] = useState(''), [preferred,setPreferred] = useState('');
  const [busy,setBusy] = useState(false), [dirty,setDirty] = useState(false), [status,setStatus] = useState('正在读取人物…');
  const select = (entity:Entity) => { setId(entity.id); setAliases((entity.aliases || []).join('\n')); setPreferred(entity.preferred_name || ''); setDirty(false); onDirtyChange(false); };
  useEffect(() => {
    let active = true;
    cognitionRequest('/other').then(data => {
      if (!active) return;
      const values = Object.fromEntries(Object.entries(data.entities as Record<string,Entity>).filter(([key])=>!['human','k'].includes(key)));
      setEntities(values);
      const first = Object.values(values)[0];
      if (first) select(first);
      setStatus(first ? '' : '尚无外部实体，请先在他者书登记人物。');
    }).catch(error=>{if(active)setStatus(error.message);});
    return ()=>{active=false;};
  }, []);
  const changed = () => {setDirty(true);onDirtyChange(true);setStatus('');};
  const names = Array.from(new Set(aliases.split(/\r?\n/).map(x=>x.trim()).filter(Boolean)));
  const entity = entities[id];
  return <section className="entity-names-editor" aria-label="人物称呼">
    <p>同一个人使用同一份实体档案。这里的别名用于认知与检索，不修改连接权限、历史原文，也不合并已有实体。</p>
    <form onSubmit={async event=>{
      event.preventDefault(); if (!entity || busy) return;
      setBusy(true);setStatus('正在保存…');
      try {
        const value:Entity = await cognitionRequest(`/other/entities/${encodeURIComponent(id)}/names`, {aliases:names, preferred_name:preferred, revision:entity.revision}, 'PUT');
        setEntities(current=>({...current,[id]:value})); select(value);setStatus('已保存，下次认知构建和检索使用新称呼。');
      } catch(error) {setStatus(error instanceof Error ? error.message : '未确认保存成功');}
      finally {setBusy(false);}
    }}>
      <label>人物<select disabled={busy || !entity} value={id} onChange={event=>{
        if(dirty && !window.confirm('切换人物会放弃未保存的称呼，继续？')) return;
        select(entities[event.target.value]);setStatus('');
      }}>{Object.values(entities).map(e=><option value={e.id} key={e.id}>{e.name}</option>)}</select></label>
      {entity && <>
        <p>主名称：{entity.name} · 只编辑称呼，不改变人物身份。</p>
        <label>其他称呼（每行一个，最多 16 个）<textarea rows={5} disabled={busy} value={aliases} maxLength={1100} onChange={event=>{
          setAliases(event.target.value);changed();
          if(preferred !== entity.name && !event.target.value.split(/\r?\n/).map(x=>x.trim()).includes(preferred)) setPreferred('');
        }} placeholder="填写你已确认属于这个人的中文名、英文名或昵称" /></label>
        <label>常用称呼<select aria-label="常用称呼" disabled={busy} value={preferred} onChange={event=>{setPreferred(event.target.value);changed();}}>
          <option value="">不指定</option>{Array.from(new Set([entity.name,...names])).map(n=><option key={n} value={n}>{n}</option>)}
        </select></label>
        <small>常用称呼作为客观身份资料提供给 AI，不强制每句使用同一种叫法。家庭成员请分别编辑，不要互填成别名。</small>
        <button type="submit" disabled={busy || !dirty}>{busy?'保存中…':'保存称呼'}</button>
      </>}
    </form>
    <p role="status">{status}</p>
  </section>;
}
