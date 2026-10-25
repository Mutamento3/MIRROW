import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import CognitionReviewIndex, { reviewStatus } from './CognitionReviewIndex';
import { ReadingBody } from './CognitionReadingBody';
import HabitReviewStatus, { type HabitRunStatus } from './HabitReviewStatus';

type Plan = { id: string; domain: string; name: string; before: string; after: string; revision: string; related?: { name: string; reason: string }[]; [key: string]: unknown };
type Source = { quote?: string; speaker?: string };
type Item = { id: string; status: string; item_revision: string; source_date: string; manual_action?: string; restored_at?: string; evidence_warning?: string; source_bundle_id?: string; proposal: { name: string; target_entry: string; domain: string; body: string; evidence: Source[] }; plan: Plan; reason: string };
type LegacyHint = { id: string; proposed_name: string; target_entry: string; proposed_body: string; reason: string; source: string };
const labels: Record<string,string> = { completed:'已整理',cached:'已是最新',pending:'等待整理',completed_with_rejections:'部分候选已隔离',running:'整理中',error:'整理未完成',not_run:'尚未整理',interrupted:'上次整理中断',no_evidence:'暂无新材料' };
const laneLabels: Record<string,string> = {world:'世界书',self:'自我书',interest:'兴趣爱好',other:'他者书',subjective:'自我／他者书',brief:'认知摘要',wander:'漫想自省'};

export default function CognitionMaintenance({ domainFilter, entryNameFilter, entryAliases = [], initialFilter = 'all' }: { domainFilter?: string; entryNameFilter?: string; entryAliases?: string[]; initialFilter?: string }) {
  const [items,setItems] = useState<Item[]>([]);
  const [legacyHints,setLegacyHints] = useState<LegacyHint[]>([]);
  const [sourceBundle,setSourceBundle] = useState<{itemId:string;evidence:Source[]}|null>(null);
  const [run,setRun] = useState<HabitRunStatus & {source_date:string;lanes?:Record<string,string>;retired_lanes?:Record<string,string>;memory_status?:string;wander_processed?:number;lane_errors?:Record<string,{message:string}>;lane_rejections?:Record<string,{proposal_count:number;review_count:number}>}>({status:'not_run',source_date:''});
  const [index,setIndex] = useState(0);
  const [filter,setFilter] = useState(domainFilter === 'world' && initialFilter === 'pending' ? 'observing' : initialFilter);
  const [version,setVersion] = useState(0);
  const [preview,setPreview] = useState<{plan:Plan;token:string;itemId:string;requestedTarget:string}|null>(null);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [view,setView] = useState('after');
  const [target,setTarget] = useState('');
  const [newConfirmed,setNewConfirmed] = useState(false);
  const [editingTarget,setEditingTarget] = useState(false);
  const subjective = ['self','other'].includes(domainFilter || '');
  const world = domainFilter === 'world';
  const unresolvedWorld = (i: Item) => i.proposal.domain === 'world' && ['pending','observing','isolated','error','applying'].includes(i.status);
  useEffect(() => {
    let active=true; setBusy(true); setError('');
    cognitionRequest('/maintenance').then(data => { if(active){setItems(data.records);setRun(data.run);setLegacyHints(data.legacy_world_hints||[]);setIndex(0);} })
      .catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[version]);
  const shown=items.filter(i=>(!domainFilter||i.proposal.domain===domainFilter)&&(!entryNameFilter||[entryNameFilter,...entryAliases].includes(i.proposal.target_entry||i.proposal.name))&&
    (subjective ? ['auto_applied','applied','unchanged'].includes(i.status) : filter==='all'||(filter==='pending'?['pending','applying'].includes(i.status):world?unresolvedWorld(i):i.status==='observing')));
  const item=shown[index];
  const activePreview=preview?.itemId===item?.id&&preview?.requestedTarget===target?preview:null;
  const pending=!subjective && !!item && item.proposal.domain !== 'world' && ['pending','applying'].includes(item.status);
  const sourceRefs = sourceBundle && sourceBundle.itemId===item?.id ? sourceBundle.evidence : item?.proposal.evidence||[];
  useEffect(()=>{
    if(view!=='source'||!item?.source_bundle_id)return;
    let active=true;
    cognitionRequest(`/maintenance/${item.id}/sources`).then(data=>{if(active)setSourceBundle({itemId:item.id,evidence:data.evidence});})
      .catch(e=>{if(active)setError(e.message);});
    return()=>{active=false;};
  },[item?.id,item?.source_bundle_id,view]);
  useEffect(()=>{setPreview(null);setView('after');setNewConfirmed(false);setEditingTarget(false);setTarget(item?.plan.name||'');},[item?.id]);
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  useEffect(()=>{
    if(!pending||!item||!target.trim())return;
    let active=true;
    const timer=window.setTimeout(()=>{
      cognitionRequest(`/maintenance/${item.id}/preview`,{name:target,domain:item.proposal.domain,operation:'replace'})
        .then(result=>{if(active){setPreview({...result,itemId:item.id,requestedTarget:target});setNewConfirmed(false);}})
        .catch(e=>{if(active)setError(e.message);});
    },250);
    return()=>{active=false;window.clearTimeout(timer);};
  },[item?.id,target,pending]);
  async function prepare(){if(!item)return;const result=await cognitionRequest(`/maintenance/${item.id}/preview`,{name:target,domain:item.proposal.domain,operation:'replace'});setPreview({...result,itemId:item.id,requestedTarget:target});setNewConfirmed(false);}
  return <section className="cognition-review" aria-label="认知更新">
    {world&&<p role="status">世界书自动演变：有据的新事实与更正自动更新；未解决的材料保留并在后续整理时继续核对。</p>}
    <div className="review-toolbar"><span>{run.status==='error'&&Object.values(run.lanes||{}).includes('completed')?'部分完成':labels[run.status]||run.status}{run.source_date && ' · '+run.source_date}</span><button disabled={busy} onClick={()=>setVersion(n=>n+1)}>刷新</button></div>
    {run.lanes&&<p className="review-lanes">{Object.entries(run.lanes).map(([name,status])=><span key={name}>{laneLabels[name]||name}：{status==='not_triggered'?'未触发':status==='no_evidence'?'暂无新材料':labels[status]||status}　</span>)}</p>}
    {run.memory_status==='unavailable'&&<p role="status">这次没有读到历史记忆，已使用本日对话整理。</p>}
    {run.retired_lanes?.wander&&<p className="review-lanes">旧漫想入书通道已停用，历史{run.retired_lanes.wander==='error'?'失败记录和':''}材料保留，不再补跑。</p>}
    {run.lane_errors&&Object.entries(run.lane_errors).map(([name,err])=><p key={name} role="status">{laneLabels[name]||name}：{err.message}</p>)}
    {run.lane_rejections&&Object.entries(run.lane_rejections).map(([name,count])=><p key={name} role="status">{laneLabels[name]||name}：已隔离 {count.proposal_count} 条无法核实的候选{count.review_count ? `，另忽略 ${count.review_count} 条无法对应候选的审核结果` : ''}；其余内容照常完成。</p>)}
    <HabitReviewStatus run={run} />
    {error&&<p role="alert">{error}</p>}
    {!subjective&&<div className="review-toolbar"><select aria-label="显示记录" value={filter} disabled={busy} onChange={e=>{setFilter(e.target.value);setIndex(0);}}><option value="all">全部记录</option>{!world&&<option value="pending">待确认</option>}<option value="observing">{world?'未解决材料':'继续观察'}</option></select><button disabled={busy} onClick={()=>{if(window.confirm(world?'重新核对世界书及保留材料？会调用模型，核查通过后自动更新世界书。':'重新整理最近一天的对话？会调用模型并可能更新各本书。'))action(async()=>{const result=await cognitionRequest(world?'/maintenance/run-world':'/maintenance/run',world?{source_date:run.source_date}:{},'POST',420000);setVersion(n=>n+1);if(result.status==='error')throw new Error('世界书未全部整理完成，保留材料可重试。');});}}>{world?'重新演变':'重新整理'}</button></div>}
    {busy&&<p role="status">处理中…</p>}
    {!!shown.length&&<CognitionReviewIndex items={shown} index={index} disabled={busy} onSelect={setIndex} />}
    {item ? <article className="review-detail-card" aria-label={item.plan.name} aria-busy={busy}>
      <header className="review-card-heading"><div><span className="review-status-pill">{item.proposal.domain==='world'&&item.status==='pending'?'继续观察':reviewStatus[item.status]||'更新记录'}</span><h3>{item.plan.name}</h3></div><span>{index+1} / {shown.length}</span></header>
      {world&&unresolvedWorld(item)&&<p className="review-save-note">{item.status==='applying'?'保存状态待恢复确认，材料与写入记录均已保留。':'材料已保留，尚未写入正式事实。'}{item.reason}</p>}
      {item.manual_action==='delete'&&<p>{item.restored_at?'已恢复':'已删除，原文保留在备份中'}</p>}
      {item.evidence_warning&&<p className="review-save-note" role="status">{item.evidence_warning}</p>}
      <div className="review-tabs">{[['after',pending?'更新后':'内容'],['before','原来'],...(!subjective?[['source','原话']]:[])].map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</div>
      <div className="review-body">{view==='source'?(sourceRefs.length?sourceRefs.map((e,i)=><blockquote key={i}>{e.quote}</blockquote>):<p>{item.source_bundle_id?'正在读取保留的原始材料…':'这条记录没有附带原话。'}</p>):<ReadingBody body={view==='before'?(activePreview?.plan.before??item.plan.before)||'新条目':activePreview?.plan.after??item.plan.after} />}</div>
      {pending&&<div className="review-target"><div className="review-destination"><span>存到 · {activePreview?.plan.name||target}</span><button aria-label="修改保存位置" aria-expanded={editingTarget} disabled={busy||item.status==='applying'} onClick={()=>setEditingTarget(v=>!v)}>{editingTarget?'收起':'修改'}</button></div>{editingTarget&&<label>词条名称<input value={target} disabled={busy||item.status==='applying'} onChange={e=>{setTarget(e.target.value);setPreview(null);}} /></label>}{activePreview?.plan.related?.filter(r=>r.name!==activePreview.plan.name).map(r=><button key={r.name} disabled={busy} onClick={()=>{setTarget(r.name);setPreview(null);setView('before');}}>查看「{r.name}」作为目标</button>)}</div>}
      {activePreview?.plan.revision==='new'&&!!activePreview.plan.related?.length&&<label className="review-new-confirm"><input type="checkbox" checked={newConfirmed} onChange={e=>setNewConfirmed(e.target.checked)} />不是同一对象，保留为新条目</label>}
      {pending&&!!activePreview?.plan.before&&<p className="review-save-note">采用后替换目标正文，不会自动删除其他词条。</p>}
      <div className="review-actions"><button disabled={busy||index===0} onClick={()=>setIndex(n=>n-1)}>上一条</button>
        {item.manual_action==='delete'&&!item.restored_at&&<button disabled={busy} onClick={()=>action(async()=>{await cognitionRequest(`/deleted/${item.id}/restore`,{},'POST');setVersion(n=>n+1);})}>恢复条目</button>}
        {pending&&(activePreview?<button className="btn-primary" disabled={busy||!!item.evidence_warning||(activePreview.plan.revision==='new'&&!!activePreview.plan.related?.length&&!newConfirmed)} onClick={()=>action(async()=>{await cognitionRequest(`/maintenance/${item.id}/decide`,{plan:activePreview.plan,token:activePreview.token,action:'apply',new_entity_confirmed:newConfirmed});setVersion(n=>n+1);})}>{item.status==='applying'?'继续保存':'采用'}</button>:<button className="btn-primary" disabled={busy||!target.trim()||!!item.evidence_warning} onClick={()=>action(prepare)}>{error ? '重试' : '正在核对…'}</button>)}
        {pending&&<button disabled={busy||item.status==='applying'} onClick={()=>action(async()=>{await cognitionRequest(`/maintenance/${item.id}/decide`,{action:'reject',item_revision:item.item_revision});setVersion(n=>n+1);})}>不采用</button>}
        <button disabled={busy||index>=shown.length-1} onClick={()=>setIndex(n=>n+1)}>下一条</button></div>
    </article>:!busy&&<p>{subjective?'暂无更新记录':world&&filter==='observing'?'暂无未解决材料':filter==='pending'?'没有需要确认的内容':'暂无记录'}</p>}
    {world&&!!legacyHints.length&&<section aria-label="历史世界书材料"><h3>历史待核材料 · {legacyHints.length}</h3><p>旧建议已保留为线索，后续整理会结合原文核对；这些摘要本身不作为事实依据。</p>{legacyHints.map(hint=><details key={hint.id}><summary>{hint.proposed_name||hint.target_entry||'历史材料'}</summary><ReadingBody body={hint.proposed_body}/><p>{hint.reason}</p>{hint.source&&<p>历史来源说明：{hint.source}</p>}</details>)}</section>}
  </section>;
}
