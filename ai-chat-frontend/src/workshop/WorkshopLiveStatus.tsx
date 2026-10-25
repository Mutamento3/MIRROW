import type { Job } from './types';
import WorkshopCollaboration, {type Collaboration} from './WorkshopCollaboration';

const labels:Record<string,string>={queued:'已排队',account_paused:'等待开工条件',discussing:'工具手正在讨论',executing:'工具手正在制作',awaiting_input:'等待 AI 回访',awaiting_confirmation:'等待 AI 确认方案',awaiting_review:'等待质检与 AI 验收',budget_paused:'预算暂停',paused:'已暂停',stopping:'正在保存现场',recovery_required:'现场待核对',accepted:'AI 已验收',canceled:'已取消'};
const reasons:Record<string,string>={usage_unknown:'中断后的用量回执待核对，尚未认定额度耗尽',token_budget:'本工单累计用量达到上限',desktop_busy:'本机桌面 Codex 正在执行任务，空闲后自动续跑',desktop_unknown:'桌面运行状态暂无法核对，确认空闲后续跑',calibration_limit:'达到本次批准的校准增量',calibration_unavailable:'官方额度窗口暂无法核对',calibration_reset:'校准窗口变化，样本未采用',calibration_expired:'本次获批的校准工作时限已结束',quota_low:'账号剩余额度低于开工门槛',quota_unknown:'账号剩余额度待核对'};

export function liveLabel(job:Job, desktop?:{status:string;ready:boolean}, idleOnly=true){
  if(job.state==='awaiting_input' && job.reason==='k_decision_required')return '等待 AI 回答工具手';
  if(job.state==='recovery_required' && job.reason==='execution_no_delivery')return '制作受阻 · 尚未交付';
  if(job.state==='budget_paused' && job.reason==='usage_unknown')return '核对用量 · 暂停';
  if(['queued','account_paused'].includes(job.state) && idleOnly && desktop && !desktop.ready)
    return desktop.status==='busy'?'等待桌面空闲':'核对桌面状态';
  if(job.state==='awaiting_review'){
    if(job.lightweight_ready)return '已交付 · 可欣赏';
    if(job.quality_summary?.some(row=>row.status==='running'))return 'Luna 正在质检';
    if(job.quality_summary?.some(row=>row.status==='queued'))return desktop && !desktop.ready && idleOnly?'质检等待桌面空闲':'Luna 质检排队';
    if(job.quality_summary?.some(row=>['failed','budget_paused','interrupted'].includes(row.status)))return '交付检查受阻';
    if(job.quality_summary?.length && job.quality_summary.every(row=>['completed','skipped'].includes(row.status)))return '质检已返回 · 等 AI 验收';
  }
  return labels[job.state] || job.state;
}

export default function WorkshopLiveStatus({job,desktop,idleOnly,checkedAt,error,floating=false,pool,jobs,account,collaboration}:{collaboration?:Collaboration|null;pool?:{capacity:number;running:{job_id:string;kind:string}[]};jobs?:Job[];account?:{bucket:string;observed_percent:number|null;window_mins?:number;active:boolean}[];job?:Job;desktop?:{status:string;ready:boolean};idleOnly:boolean;checkedAt:number|null;error:string;floating?:boolean}){
  const stale=!checkedAt || Date.now()-checkedAt>8000 || Boolean(error);
  const label=stale?'连接待恢复':job?liveLabel(job,desktop,idleOnly):'暂无进行中的工单';
  const paused=job && ['budget_paused','paused','recovery_required'].includes(job.state);
  const working=job && ['executing','discussing'].includes(job.state);
  reasons.k_decision_required='工具手有一个明确问题待回答；邀请 AI 查看问题后，用问题答复继续，已有方案确认保留';
  const qualityReason=job?.state==='awaiting_review'?job.quality_summary?.find(row=>['failed','budget_paused','interrupted'].includes(row.status))?.reason:'';
  const detail=stale?'当前画面可能已过期，正在重新读取；恢复连接后立即更新':job?(job.reason==='execution_no_delivery'?'本版没有交付，方案确认仍保留；展开制作节点查看工具回复与卡点':qualityReason || reasons[job.reason] || job.waiting_reasons?.join('；') || (working?'进度与阶段记录会自动更新':'查看工单可展开每轮记录')):'可以邀请 AI 来工作坊';
  return <aside className={`workshop-live-status${floating?' workshop-live-status--scene':''}`} data-state={stale?'offline':paused?'paused':working?'working':'waiting'}>
    {collaboration && !stale && <WorkshopCollaboration value={collaboration}/>}
    <div role="status" aria-live="polite"><i aria-hidden="true"/><strong>{label}</strong></div>
    {pool && <div className="workshop-pool"><strong>运行 {pool.running.length} / {pool.capacity}</strong>{pool.running.map(lane=>{const row=jobs?.find(item=>item.id===lane.job_id);return <p key={lane.job_id}>{lane.kind==='review'?'🔎 质检':'🛠️ 制作 / 讨论'} · {row?.visibility==='private'?'保密工单':row?.title || '工单'}{row && lane.kind!=='review'?` · ${liveLabel(row,desktop,idleOnly)}`:''}</p>;})}</div>}
    {pool && pool.capacity>1 && idleOnly && !!account?.length && <p>工作坊账号下降：{account.map(row=>`${row.observed_percent===null?'待采样':`${row.observed_percent}%`}（${row.window_mins || '?'} 分钟窗口）`).join('；')}</p>}
    {job && <p className="workshop-live-title">{job.visibility==='private'?'保密工单':job.title}</p>}
    <p>{detail}</p>
    {job?.budget_summary?.mode==='observe_only' && <p>预算限制暂关 · 用量继续记录</p>}
    {job?.account_usage && <p title={job.account_usage.note}>{job.account_usage.status==='disabled'?'直接开工 · 不统计账号额度下降':job.account_usage.status==='parallel_workshop'?'并行 · 本单仅统计实际 token':job.account_usage.status==='parallel_native_work'?'其他并行任务期间暂停额度归因':<>开工段账号下降：{job.account_usage.observed_percent===null?'等待采样':`${job.account_usage.observed_percent}%（观测）`}{job.account_usage.active?' · 记录中':' · 暂停段不计入'}</>}</p>}
    <small>{checkedAt?`最近同步 ${new Date(checkedAt).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}`:'尚未收到状态'} · 前台约 2 秒刷新</small>
  </aside>;
}
