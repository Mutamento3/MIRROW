import { useState } from 'react';
import type { Inspection, ProviderInspection, InspectionSection } from './contextTypes';

const layerLabels:Record<string,string> = {stable_prefix:'Builder 静态前缀',mixed_system:'混合系统段',history_or_interleaved:'历史 / 穿插消息',dynamic:'动态尾段',tail:'尾部提醒'};
export default function ContextMessages({snapshot,provider,onSection}:{snapshot:Inspection;provider?:ProviderInspection;onSection:(section:InspectionSection)=>void}) {
  const [mode,setMode] = useState<'provider'|'builder'>('provider');
  const [format,setFormat] = useState<'read'|'json'>('read');
  const actual = mode === 'provider';
  const messages = actual ? provider?.messages : snapshot.messages;
  return <>
    <div className="context-view-options"><div className="monitor-filters"><button aria-pressed={actual} onClick={()=>setMode('provider')}>发送给模型</button><button aria-pressed={!actual} onClick={()=>setMode('builder')}>Builder 输出</button></div><div className="monitor-filters"><button aria-pressed={format==='read'} onClick={()=>setFormat('read')}>阅读视图</button><button aria-pressed={format==='json'} onClick={()=>setFormat('json')}>消息 JSON</button></div></div>
    {actual && !provider ? <p className="context-notice">此构建还没有关联的发送快照。不能用 Builder 输出代替最终消息；更新后端后，下一次真实调用才会产生新记录。</p> : (actual ? !provider?.body_available : !snapshot.body_available) ? <p className="context-notice">正文未保留，无法提供完整预览。</p> : <>
      <p className="context-caption">{actual ? '请求发出前的 messages 数组，包含调度器追加内容。调用归属不代表 Builder 内容完整一致，请在组装核对中检查遗漏。' : 'Builder 输出包含系统段、历史、动态尾段和尾部提醒；不包含调用方后续追加内容。'} {snapshot.body_note}。位置从 #1 开始，字符偏移按诊断文本显示。</p>
      {format==='json' ? <pre className="context-full-text">{JSON.stringify((messages || []).map(message=>Object.fromEntries(Object.entries(message).filter(([key])=>!['section','layer'].includes(key)))),null,2)}</pre> : <div className="context-message-list">{messages?.map((message,index)=>{
        const match = actual ? provider?.builder_message_map.find(item=>item.provider_index===index+1) : undefined;
        const builderIndex = actual ? match?.builder_index : index+1;
        const built = builderIndex ? snapshot.messages[builderIndex-1] : undefined;
        const fields = snapshot.sections.filter(section=>section.placements.some(place=>place.message_index===builderIndex));
        const content = typeof message.content==='string' ? message.content : JSON.stringify(message.content,null,2) ?? '';
        const extra = Object.fromEntries(Object.entries(message).filter(([key])=>!['role','content','section','layer','reasoning_content'].includes(key)));
        const stableText = built?.layer==='stable_prefix' && typeof built.content==='string' ? built.content : '';
        const canMarkBoundary = stableText && content.startsWith(stableText);
        return <article className="context-message" key={index}>
          <header><strong>#{index+1}</strong><code>{message.role}</code><span>{built ? layerLabels[built.layer || ''] || built.section : '调用方追加 / 未映射'}</span><small>{content.length.toLocaleString()} 字符</small></header>
          {match?.match==='extended_system' && <p className="context-notice">这条 system 在 Builder 前缀后追加了调度器内容。静态前缀边界不等于整条 system 的末尾。</p>}
          <div className="context-field-chips">{fields.map(field=><button key={field.ingredient} onClick={()=>onSection(field)}>{field.name}</button>)}</div>
          <details open={index===0}><summary>查看完整消息内容</summary>
            {canMarkBoundary ? <><pre className="context-full-text">{stableText}</pre><div className="context-boundary">静态前缀声明结束 · 消息 #{index+1} · 字符 {stableText.length}<small>这是 Builder 的稳定段策略，不是 provider 已命中的缓存长度。</small></div>{content.length>stableText.length && <pre className="context-full-text">{content.slice(stableText.length)}</pre>}</> : <pre className="context-full-text">{content}</pre>}
          </details>
          {message.reasoning_content && <details><summary>历史 reasoning_content 字段</summary><pre className="context-full-text">{message.reasoning_content}</pre></details>}
          {Object.keys(extra).length > 0 && <details><summary>其他消息字段（工具调用等）</summary><pre className="context-full-text">{JSON.stringify(extra,null,2)}</pre></details>}
        </article>;
      })}</div>}
    </>}
  </>;
}
