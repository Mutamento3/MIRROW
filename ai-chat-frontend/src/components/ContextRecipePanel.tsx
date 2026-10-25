import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBase } from '../config';
import ContextMessages from './monitor/ContextMessages';
import ContextSectionDialog from './monitor/ContextSectionDialog';
import { statusLabel, type RecipeCall, type RecipeEntry, type Inspection, type InspectionSummary, type InspectionSection } from './monitor/contextTypes';
import { timeText } from './monitor/monitorModel';
import './monitor/ContextInspection.css';
export type { RecipeCall } from './monitor/contextTypes';

export default function ContextRecipePanel({recipeCallLog,recipeCallLogObservation}: {recipeCallLog:Record<string,RecipeCall>;recipeCallLogObservation?:{status?:string}}) {
  const [recipes,setRecipes]=useState<RecipeEntry[]>([]);
  const [calls,setCalls]=useState<InspectionSummary[]>([]);
  const [recipe,setRecipe]=useState('FULL_CHAT');
  const [snapshot,setSnapshot]=useState<Inspection|null>(null);
  const [providerId,setProviderId]=useState('');
  const [view,setView]=useState('messages');
  const [filter,setFilter]=useState('all');
  const [query,setQuery]=useState('');
  const [section,setSection]=useState<InspectionSection|null>(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [selectedId,setSelectedId]=useState('');
  const selection=useRef(0);
  const controller=useRef<AbortController|null>(null);
  const refresh = useCallback(async()=>{
    setError('');
    const results=await Promise.allSettled([
      fetch(`${getApiBase()}/recipes`).then(async response=>{if(!response.ok)throw new Error('配方目录读取失败');const data=await response.json();if(data.error || !Array.isArray(data.recipes))throw new Error('配方目录读取失败');setRecipes(data.recipes);}),
      fetch(`${getApiBase()}/api/debug/context-inspections`,{cache:'no-store'}).then(async response=>{if(!response.ok)throw new Error('新版组装快照尚不可用，请确认后端已更新并重启。');const data=await response.json();setCalls(data.items || []);}),
    ]);
    const failed=results.find(result=>result.status==='rejected');
    if(failed?.status==='rejected')setError(failed.reason instanceof Error?failed.reason.message:'读取失败');
  },[]);
  useEffect(()=>{void refresh();return()=>{controller.current?.abort();};},[refresh]);
  const selectCall=async(id:string)=>{
    const version=++selection.current;
    controller.current?.abort();
    const request=new AbortController();controller.current=request;
    setSelectedId(id);setSnapshot(null);setSection(null);setError('');
    if(!id){setLoading(false);return;}
    setLoading(true);
    try{
      const response=await fetch(`${getApiBase()}/api/debug/context-inspections/${encodeURIComponent(id)}`,{signal:request.signal,cache:'no-store'});
      if(!response.ok)throw new Error(response.status===404?'此快照已过期，刷新列表后重新选择。':'快照读取失败');
      const data:Inspection=await response.json();
      if(version===selection.current){setSnapshot(data);setProviderId(data.providers.at(-1)?.request_id || '');}
    }catch(cause){if(version===selection.current && !request.signal.aborted)setError(cause instanceof Error?cause.message:'读取失败');}
    finally{if(version===selection.current)setLoading(false);}
  };
  const definition=recipes.find(item=>item.key===recipe);
  const options=calls.filter(call=>call.recipe===recipe);
  const provider=snapshot?.providers.find(item=>item.request_id===providerId);
  const legacy=Object.values(recipeCallLog).find(item=>item.recipe===definition?.name || (recipe==='FULL_CHAT' && item.scenario==='主聊天（完整）'));
  const sections=(snapshot?.sections || []).filter(item=>(filter==='all' || (filter==='omitted' ? ['gated','empty','disabled','shadow_preview','error','tail'].includes(item.status || '') : item.placements.length>0)) && `${item.name} ${item.ingredient}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="context-inspection">
    {recipeCallLogObservation?.status==='error' && <p role="status">历史调用日志观测失败，调用状态暂未确认。</p>}
    <div className="context-selector">
      <label>场景<select aria-label="上下文场景" value={recipe} onChange={event=>{setRecipe(event.target.value);void selectCall('');}}>
        {!recipes.some(item=>item.key==='FULL_CHAT') && <option value="FULL_CHAT">AI 私聊 · FULL_CHAT</option>}
        {recipes.map(item=><option key={item.key} value={item.key}>{item.consumer || item.name} · {item.key}</option>)}
      </select></label>
      <label>本次构建<select aria-label="选择构建快照" value={selectedId} onChange={event=>void selectCall(event.target.value)}><option value="">选择一次真实构建（{options.length}）</option>{options.map(call=><option value={call.call_id} key={call.call_id}>{timeText(call.timestamp)} · {call.provider_count} 次发送</option>)}</select></label>
      <button onClick={()=>void refresh()}>刷新列表</button>
    </div>
    {error && <p className="context-notice" role="alert">{error}</p>}
    {loading && <p role="status">正在读取该次完整快照…</p>}
    {!snapshot && !loading && <div className="context-empty"><h3>{definition?.name || 'AI 私聊'}</h3><p>{definition?.pipeline==='legacy_manual'?'此配方声明使用旧手工管道，不应标成 Builder 已接入。':options.length?'选择一次构建后，查看它对应的字段和发送消息。':'当前没有新版构建快照。后端更新后，需要一次真实调用才能查看，打开监控页不会主动调用模型。'}</p>
      {legacy && <details><summary>查看旧版快照（只覆盖部分 Builder 输出）</summary><p>旧记录没有完整发送消息、可靠前缀边界或空值诊断，不能据此判断完整接入。</p><p>记录标签：{legacy.via_builder===true?'经过 Builder':legacy.via_builder===false?'手工构建自报':'未记录'}</p>{legacy.msg_order?.map(message=><details key={message.idx}><summary>#{message.idx} · {message.role} · {message.section}</summary><pre className="context-full-text">{message.preview}</pre></details>)}</details>}
      <details><summary>查看配方声明</summary><p>声明顺序不是最终消息顺序；声明存在也不证明已运行。</p>{definition?.sections.map((item,index)=><p key={item.ingredient}>{index+1}. {item.name} <code>{item.ingredient}</code> {item.condition ? '条件：'+item.condition : ''}</p>)}</details>
    </div>}
    {snapshot && <>
      <div className="context-proof"><div><small>构建证据</small><strong>Builder 已记录</strong><span>{timeText(snapshot.timestamp)}</span></div><div><small>本次发送</small><strong>{provider ? provider.status==='response_received'?'已收到模型响应':provider.status==='submitted'?'已准备提交 / 等待响应':'未收到有效响应':'尚无关联记录'}</strong><span>{provider?.model || '不推断模型状态'}</span></div><div><small>稳定分界</small><strong>{snapshot.dynamic_last?'静态前缀 / 动态后移':'混合排列'}</strong><span>缓存命中位置未由此数据证明</span></div></div>
      <div className="context-request-select"><label>模型请求<select aria-label="选择模型请求" value={providerId} onChange={event=>{setProviderId(event.target.value);setSection(null);}}><option value="">只查看构建</option>{snapshot.providers.map((item,index)=><option value={item.request_id} key={item.request_id}>请求 {index+1} · {timeText(item.timestamp)} · {item.model} · {item.message_count} 条消息</option>)}</select></label><button onClick={()=>void selectCall(snapshot.call_id)}>更新这份快照</button></div>
      <p className="context-caption">当前内容固定，列表刷新不会替换它。内存保留最近 6 次构建、30 分钟内的快照，每次最多保留最近 3 次发送；不是永久调用历史。</p>
      <nav className="monitor-filters" aria-label="上下文核对视图">{[['messages','消息顺序与全文'],['fields','字段与门控'],['assembly','组装核对']].map(([key,label])=><button key={key} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</nav>
      {view==='messages' && <ContextMessages snapshot={snapshot} provider={provider} onSection={setSection}/>}
      {view==='fields' && <><div className="context-field-tools"><input aria-label="搜索上下文字段" value={query} onChange={event=>setQuery(event.target.value)} placeholder="中文名称或字段名…"/><select aria-label="筛选注入状态" value={filter} onChange={event=>setFilter(event.target.value)}><option value="all">全部字段</option><option value="injected">有 Builder 落点</option><option value="omitted">跳过 / 空值 / 异常</option></select></div><div className="context-section-list">{sections.map(item=><button key={item.ingredient} onClick={()=>setSection(item)}><span><strong>{item.name}</strong><code>{item.ingredient}</code></span><span>{statusLabel(item.status)}</span><small>{item.placements.length ? item.placements.map(place=>'#'+place.message_index).join('、') : '无已证明落点'}</small></button>)}</div></>}
      {view==='assembly' && <><p className="context-notice">以下按精确内容核对 Builder 消息与 provider 消息。未匹配项不会默认归入消息 #1；有记录也不等于整体接入完整。</p>{provider ? <div className="context-section-list">{provider.builder_message_map.map(item=><div key={item.builder_index}><strong>Builder #{item.builder_index}</strong><span>{item.provider_index?'→ 发送 #'+item.provider_index:'未找到一致消息'}</span><small>{item.match==='exact'?'消息 role/content 一致':item.match==='extended_system'?'原前缀保留，system 后方追加':'需核查调用方变换或遗漏'}</small></div>)}</div> : <p>尚无发送快照，不能完成核对。</p>}<details><summary>本次构建身份与保留边界</summary><code>{snapshot.call_id}</code><p>来自本次实际 Builder 输出；正文仅保留在后端内存，不进入 prompt ledger。请求失败或重启不伪造记录。图像载荷与敏感内容会隐藏。</p></details></>}
      {section && <ContextSectionDialog section={section} provider={provider} onClose={()=>setSection(null)}/>}
    </>}
  </section>;
}
