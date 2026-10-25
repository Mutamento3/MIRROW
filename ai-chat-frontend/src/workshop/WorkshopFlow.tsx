import { useState } from 'react';
import WorkshopQuality from './WorkshopQuality';
import { BudgetStatus } from './WorkshopBudget';
import type { FlowRecord, Room, WorkshopFlow as Flow } from './types';
import './WorkshopFlow.css';

const steps:Record<string,[string,string,string]> = {
  queue:['01','收单与排队','点子已收好，等待轮到这份工单'],
  discuss:['02','讨论与方案','让灵感变成可以实现的小作品'],
  confirm:['03','AI 确认方案','由 AI 确认这一版的范围和验收目标'],
  make:['04','制作与交付','工具手制作文件，保留实际检查记录'],
  quality:['05','Luna 质检','静态审查与实际交互测试分别记录'],
  accept:['06','AI 验收','质检报告交给 AI，再由他作出选择'],
  shelf:['07','进入作品柜','已验收作品可以打开、运行和收藏'],
  archive:['08','归档 · 留在收藏里','完成这一版，下一次灵感可以从这里接着长'],
};
const statuses = { skipped:'此作品无需此步骤', done:'有完成记录', current:'当前节点', blocked:'等待处理', pending:'尚无完成记录' };
const events:Record<string,string> = { quality_skipped:'静态作品免质检', artifact_shared:'作品分享到主聊天', accepted:'收到工单', plan:'保存方案', modified:'AI 修改需求', inspiration_added:'收到新灵感', confirmed:'AI 确认方案', delivered:'实际文件交付', delivery_referenced:'已有文件交付', quality_queued:'质检排队', quality_blocked:'质检等候', quality_report:'质检报告', inspected_browser:'AI 实际预览', inspected_text:'AI 阅读作品', accepted_work:'AI 验收作品', resource_approval:'人类伙伴批准资源', budget_calibration:'独立用量校准', budget_paused:'预算暂停', recovery_required:'现场待核对', failed:'执行未完成', paused:'已暂停', canceled:'已取消' };
const reason:Record<string,string> = { calibration_parallel_usage:'此前校准混入并行消耗，等待重新校准', calibration_required:'尚无有效百分比参考', calibration_expired:'校准阶段已结束', calibration_limit:'到达本次获批增量', calibration_reset:'校准窗口发生变化，样本未采用', calibration_unavailable:'额度窗口暂无法核对', desktop_busy:'桌面 Codex 正在执行任务，工作坊等候空闲', desktop_unknown:'桌面运行状态暂无法确认，工作坊等候核对', token_budget:'到达本工单预算', usage_unknown:'用量需要核对' };

function Records({ records, count }: { records:FlowRecord[]; count:number }) {
  return <div className="workshop-flow-records">
    {!count && <p>这个节点尚无独立回执。当前状态与已有文件显示在上方，缺少记录不代表失败。</p>}
    {count>records.length && <p>共 {count} 条 · 这里展示最近 {records.length} 条。</p>}
    {[...records].reverse().map(record=><details key={record.id}><summary><span>{events[record.kind] || record.kind}</span><time>{record.created_at.replace('T',' ').slice(5,16)}</time></summary><pre>{String(record.body.plan || record.body.text || record.body.summary || JSON.stringify(record.body,null,2))}</pre><details><summary>原始回执</summary><pre>{JSON.stringify(record.body,null,2)}</pre></details></details>)}
  </div>;
}

events.source_evidence='已读取本地身体源码';
events.completion_decided='AI 选择分享或收藏';
events.character_references_sent='工具手收到人物参考图';
events.archived='收进作品柜历史 · 依据已有验收';
events.tool_failed='工具执行失败';
events.decision_required='工具手提出补充问题';
events.decision_answered='AI 回答工具手问题';
reason.k_decision_required='等待 AI 回答工具手的问题；保留当前方案确认';
reason.execution_no_delivery='制作回合已结束但没有本版交付；方案确认仍保留，修复卡点后恢复';

export default function WorkshopFlow({room,flow,apiBase,onPreview,onUpdated}: {room:Room; flow:Flow; apiBase:string; onPreview:(artifact:Room['artifacts'][number])=>void; onUpdated:()=>void}) {
  const [detailed,setDetailed]=useState(false);
  const [expanded,setExpanded]=useState<string|null|undefined>();
  const selected=expanded===undefined ? flow.current : expanded;
  const currentArtifacts=room.artifacts.filter(item=>item.revision===flow.revision && (!flow.current_artifact_ids || flow.current_artifact_ids.includes(item.id)));
  return <section className="workshop-flow" aria-label="工单流程看板">
    <header><div><small>IDEA → LITTLE WONDER</small><h3>一件小作品的旅程</h3></div><span className="workshop-flow-version">需求 v{flow.revision}</span></header>
    <p className="workshop-flow-now">现在走到 <strong>{steps[flow.current]?.[1]}</strong>{flow.reason && <span>{reason[flow.reason] || flow.reason}</span>}</p>
    <div className="workshop-flow-chapters">{[
      {title:'需求与讨论',ids:['queue','discuss','confirm'],icon:'✎'},
      {title:'制作与检查',ids:['make','quality'],icon:'⚒'},
      {title:'AI 看成品',ids:['accept'],icon:'✧'},
      {title:'收藏与分享',ids:['shelf','archive'],icon:'▣'},
    ].map((chapter,index)=>{
      const nodes=flow.nodes.filter(node=>chapter.ids.includes(node.id));
      const current=chapter.ids.includes(flow.current);
      const done=nodes.length>0 && nodes.every(node=>['done','skipped'].includes(node.status));
      return <button type="button" key={chapter.title} data-current={current} onClick={()=>{setExpanded(current?flow.current:nodes[0]?.id);setDetailed(true);}}>
        <span aria-hidden="true">{done?'✓':chapter.icon}</span><small>0{index+1}</small><strong>{chapter.title}</strong><em>{current?'正在这里':done?'已有记录':'查看进度'}</em>
      </button>;
    })}</div>
    {!!flow.visits?.length && <details className="workshop-flow-visits">
      <summary><span>每轮往返的轨迹</span><small>{flow.visit_count} 段 · {flow.visits.filter(visit=>visit.returning).length} 次重访</small></summary>
      <p>同一节点可以再走一轮。每段保留自己的方案、补充与回执，旧轮的完成不会替代当前轮。</p>
      {(flow.visit_count || 0)>flow.visits.length && <p>显示最近四十段；更早记录仍保留在工单与旧版本中。</p>}
      <ol>{flow.visits.map(visit=><li key={visit.id} data-returning={visit.returning}>
        <span className="workshop-flow-visit-number">{visit.number}</span>
        <details><summary><span><strong>{visit.returning?'↶ ':''}{steps[visit.stage]?.[1] || visit.stage}</strong><small>需求 v{visit.revision} · {events[visit.trigger] || (visit.trigger==='input_delivered'?'开始新一轮工作':visit.trigger)}</small></span><time>{visit.started_at.replace('T',' ').slice(5,16)}</time></summary>
          {visit.returning && <p className="workshop-flow-return">{visit.from_stage===visit.stage?'再次进入这一环节':`从${steps[visit.from_stage || '']?.[1] || '上一环节'}回到这里`} · {events[visit.trigger] || '继续讨论与检查'}</p>}
          <Records records={visit.records} count={visit.count}/>
        </details>
      </li>)}</ol>
    </details>}
    <details open={detailed} onToggle={event=>setDetailed(event.currentTarget.open)}><summary>展开各环节的详细记录</summary><div className="workshop-flow-path">{flow.nodes.map(node=>{
      const [number,title,description]=steps[node.id] || ['·',node.id,'查看这个环节的记录'];
      const open=selected===node.id;
      return <article key={node.id} data-state={node.status} className="workshop-flow-node">
        <button type="button" aria-expanded={open} aria-controls={`flow-${room.job.id}-${node.id}`} onClick={()=>setExpanded(open?null:node.id)}>
          <span className="workshop-flow-dot" aria-hidden="true">{node.status==='done'?'✓':number}</span><span className="workshop-flow-label"><strong>{title}</strong><small>{description}</small></span><span className="workshop-flow-state">{statuses[node.status]}<small>{node.count} 条记录</small></span><span aria-hidden="true">{open?'−':'+'}</span>
        </button>
        {open && <div id={`flow-${room.job.id}-${node.id}`} className="workshop-flow-detail">
          {node.id==='queue' && <><p>{room.job.brief}</p>{room.job.waiting_reasons?.map(item=><p key={item}>{item}</p>)}</>}
          {node.id==='discuss' && <>{room.job.plan && <details open><summary>当前方案 v{room.job.plan_version}</summary><pre>{room.job.plan}</pre></details>}<details><summary>房间对话 · 当前页末十条（含历史版本）</summary>{room.messages.slice(-10).map(message=><div key={message.seq}><small>{({k:'AI',codex:'工具手 Codex',human:'人类伙伴',desktop_codex:'桌面 Codex'} as Record<string,string>)[message.role] || message.role} · {message.created_at}</small><pre>{message.body}</pre></div>)}</details><p>完整分页记录在下方“工单对话”；人类伙伴的补充与 AI 的确认各自保留。</p></>}
          {node.id==='confirm' && <p>{node.status==='done'?'已保存 AI 对当前方案的结构化确认。':'等待 AI 的结构化确认；普通对话和人类伙伴补充不会代替确认。'}</p>}
          {node.id==='make' && <>{currentArtifacts.map(item=><div key={item.id}><strong>{item.name}</strong><p>{item.summary}</p><details><summary>制作检查</summary><pre>{item.checks}</pre></details><button onClick={()=>onPreview(item)}>打开阶段交付</button></div>)}<details open={node.status==='blocked'}><summary>工具手最近的执行回复</summary>{room.messages.filter(message=>message.role==='codex').slice(-3).map(message=><div key={message.seq}><small>{message.created_at}</small><pre>{message.body}</pre></div>)}</details></>}
          {node.id==='quality' && currentArtifacts.map(item=><WorkshopQuality key={item.id} quality={item.quality} jobId={room.job.id} revision={room.job.revision} artifactId={item.id} apiBase={apiBase} allowReview={false} onUpdated={onUpdated}/>)}
          {node.id==='accept' && <p>{flow.lightweight_ready?'这类小作品无需等待签收，AI 可以自由回看、分享或提改进。':'报告已返回代表质检环节完成；实际测试是否通过、AI 是否验收，分别以报告和验收回执为准。'}</p>}
          {node.id==='shelf' && <p>{flow.lightweight_ready?'静态作品已入柜，可以欣赏或补充灵感；没有代替 AI 验收。':node.status==='done'?'已验收作品留在作品柜；你仍可以补充灵感，再做一版。':'当前版本还没有验收记录。旧版已验收作品继续保留在作品柜。'}</p>}
          {node.id==='archive' && <><p>{flow.archive?.archived?'这一版已归档，退出当前待办。作品、对话和每次交付都留在收藏里；补充灵感后沿原单开启下一版。':'这一版尚未归档。旧版归档与验收保留在历史里，不代替当前版本完成。'}</p>{flow.archive?.basis_at && <small>验收依据时间 · {flow.archive.basis_at.replace('T',' ')}。逐文件的检查与验收仍看各自回执。</small>}</>}
          {node.count>0 && <small>下方保留这个节点各轮的记录；当前完成状态只依据最新方案与交付。</small>}
          <Records records={node.records} count={node.count}/>
        </div>}
      </article>;
    })}</div></details>
    <details className="workshop-flow-resources"><summary>资源、暂停与恢复 · {flow.control_count} 条记录</summary><BudgetStatus budget={room.job.budget_summary}/><Records records={flow.controls} count={flow.control_count}/></details>
    {flow.history_count>0 && <details className="workshop-flow-history"><summary>旧版本的旅程 · {flow.history_count} 条回执</summary><p>按原需求版本保留，不计作这一版完成。展示最近八个历史版本，各节点最近二十条。</p>{flow.history?.map(version=><details key={version.revision}><summary>需求 v{version.revision}</summary>{version.nodes.map(node=><details key={node.id}><summary>{steps[node.id]?.[1] || '资源与执行记录'} · {node.count} 条</summary><Records records={node.records} count={node.count}/></details>)}</details>)}</details>}
    <footer>只读进度看板 · 点击节点展开 · 随工单记录自动更新</footer>
  </section>;
}
