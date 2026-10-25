import { useEffect, useRef } from 'react';
import { statusLabel, type InspectionSection, type ProviderInspection } from './contextTypes';

const diagnosticNames:Record<string,string> = {selected_entities:'选中的实体',core_only:'仅核心词条',entities:'各实体筛选',entity_id:'实体编号',eligible_entries:'有效候选条目数',selected_entries:'选入条目数',has_naming_metadata:'包含称呼资料',legacy_retired:'旧兼容通道已退役',legacy_profile_enabled:'旧画像参数开启',error_type:'异常类型'};
function diagnosticText(value:unknown):string {
  if (Array.isArray(value)) return value.length ? value.map(diagnosticText).join('\n') : '无';
  if (value && typeof value==='object') return Object.entries(value).map(([key,item])=>`${diagnosticNames[key] || key}：${diagnosticText(item)}`).join('；');
  return typeof value==='boolean' ? value?'是':'否' : String(value ?? '未记录');
}
export default function ContextSectionDialog({section,provider,onClose}:{section:InspectionSection;provider?:ProviderInspection;onClose:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(()=>{const element=dialog.current;element?.showModal();return()=>element?.close();},[]);
  return <dialog className="context-section-dialog" ref={dialog} aria-label={`${section.name}字段详情`} onCancel={onClose} onClick={event=>{if(event.target===event.currentTarget)onClose();}}><div>
    <header><h2>{section.name}</h2><button onClick={onClose} autoFocus aria-label="关闭字段详情">×</button></header>
    <code>{section.ingredient}</code><p>{section.explanation}</p>
    <dl className="monitor-facts"><div><dt>本次状态</dt><dd>{statusLabel(section.status)}</dd></div><div><dt>本次原因</dt><dd>{section.reason}</dd></div><div><dt>来源入口</dt><dd>{section.source}</dd></div><div><dt>配方位置</dt><dd>第 {section.recipe_order} 项 · {section.group}</dd></div><div><dt>门控</dt><dd>{section.condition ? `${section.condition} = ${section.condition_value ? 'true（通过）':'false（未通过）'}` : '无配方条件门控；原料内部仍可能返回空'}</dd></div><div><dt>稳定段策略</dt><dd>{section.stable_candidate?'属于稳定段白名单':'不属于稳定段白名单'}，实际落点以本次消息为准。</dd></div>
    <div><dt>Builder 落点</dt><dd>{section.placements.length ? section.placements.map(place=>`#${place.message_index} · 字符 ${place.start}–${place.end ?? '?'}`).join('；') : '没有已证明的消息落点'}</dd></div>
    <div><dt>发送消息落点</dt><dd>{section.placements.length ? section.placements.map(place=>{const match=provider?.builder_message_map.find(item=>item.builder_index===place.message_index);return match?.provider_index ? `#${match.provider_index}（${match.match==='exact'?'消息一致':'前缀保留、后方追加'}）` : '未匹配，不能证明已发送';}).join('；') : '未证明注入'}</dd></div>
    {Object.entries(section.diagnostics || {}).map(([key,value])=><div key={key}><dt>{diagnosticNames[key] || key}</dt><dd>{diagnosticText(value)}</dd></div>)}</dl>
    <h3>本次字段内容</h3>{section.text ? <pre className="context-full-text">{section.text}</pre> : <p>{section.status==='interleaved'?'此字段产生独立消息，请按落点查看消息全文。':'本次没有文本内容。'}</p>}
  </div></dialog>;
}
