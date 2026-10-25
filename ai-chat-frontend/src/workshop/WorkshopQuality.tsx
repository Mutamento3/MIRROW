import { useState } from 'react';
import type { Quality } from './types';
import './WorkshopQuality.css';

export const reviewerLabels: Record<string,string> = { 'gpt-5.6-luna':'5.6 Luna', 'gpt-6-luna':'6.0 Luna' };
const states: Record<string,string> = { queued:'质检排队', running:'正在质检', completed:'质检报告已返回 · 待 AI 判断', failed:'质检未完成', budget_paused:'质检预算暂停', interrupted:'质检中断', obsolete:'历史版本质检', skipped:'文件可读取 · 免 Luna 质检' };
const checks: Record<string,string> = { passed:'实际交互断言通过', failed:'实际测试发现问题', preview_only:'仅预览 · 未验证交互', interaction_only:'已操作 · 尚无通过断言', not_tested:'此交付形式尚未进行交互测试' };

export default function WorkshopQuality({ quality, jobId, revision, artifactId, apiBase, allowReview, onUpdated }: { quality?:Quality|null; jobId:string; revision:number; artifactId:string; apiBase:string; allowReview:boolean; onUpdated?:()=>void }) {
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const light=quality?.body.review_mode==='light';
  const body=quality?.body;
  const assertions=body?.checks?.filter(row=>row.action==='expect_text') || [];
  return <section className="workshop-quality workshop-quality-dialogue" aria-label="交付检查对话">
    <div className="workshop-quality-request"><small>检查目标</small><p>{light?'图片、文字或静态卡片，先确认文件能正常打开。':'对照当前方案，检查作品的主要功能和边界。'}</p></div>
    <div className="workshop-quality-response"><small>{light?'◇ 工作坊 · 本地文件检查':`☾ ${quality ? reviewerLabels[quality.model] || quality.model : 'Luna'}`}</small>
      <strong>{quality ? states[quality.status] || quality.status : '尚无独立检查记录'}</strong>
      <p>{body?.execution_summary || quality?.reason || (quality?.status==='queued'?'检查已排队，结果会回到这里。':quality?.status==='running'?'正在检查，还没有最终结论。':'可以直接欣赏作品，也可以按需请 Luna 检查。')}</p>
      {body?.browser_status && !body.image_review && <p>{checks[body.browser_status] || body.browser_status}</p>}
      {!!assertions.length && <ul>{assertions.slice(0,3).map((row,index)=><li key={index}>{row.status==='passed'?'✓':'✕'} {String(row.value)}</li>)}</ul>}
      {body?.coverage_note && <p className="workshop-quality-note">{body.coverage_note}</p>}
      {quality && <details><summary>展开检查过程{body?.checks ? ` · ${body.checks.length} 步` : ''}</summary>
        {body?.basic_checks?.map(item=><p key={item}>✓ {item}</p>)}
        {(body?.summary || body?.limitations) && <><h5>{body.image_review?'Luna 的图片观察':'Luna 执行前的测试设计'}</h5><p>{body.image_review?'这是视觉初检，审美判断由 AI 保留。':'这是测试设计文字；实际结果以下方执行记录为准。'}</p><pre>{body.summary}</pre><p>未覆盖：{body.limitations || '报告未另列'}</p></>}
        {!!body?.checks?.length && <><h5>实际执行记录</h5><pre>{JSON.stringify(body.checks,null,2)}</pre></>}
        <small>累计用量：{quality.usage_known ? quality.tokens.toLocaleString() : '待核对'} tokens{light?' · 本次文件检查未调用模型':''}</small>
      </details>}
    </div>
    {allowReview && (!quality || !['queued','running'].includes(quality.status)) && <button disabled={busy} onClick={async()=>{
      setBusy(true);setNotice('');
      try {
        const response=await fetch(`${apiBase}/api/workshop/rooms/${jobId}/review`,{method:'POST',headers:{'Content-Type':'application/json','X-Workshop-UI':'settings'},body:JSON.stringify({revision,artifact_id:artifactId})});
        const body=await response.json();if(!response.ok)throw new Error(body.detail||'质检请求未保存');
        setNotice('Luna 质检已排队，报告将回到这份工单。');onUpdated?.();
      }catch(cause){setNotice(cause instanceof Error ? cause.message : '质检暂不可用');}
      finally{setBusy(false);}
    }}>{busy ? '正在保存…' : light ? '请 Luna 再看看' : quality ? '重新请 Luna 质检' : '请 Luna 质检'}</button>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
