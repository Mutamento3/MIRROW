import { useState } from 'react';
import type { BudgetSummary, Job, BudgetReference } from './types';

const windowLabel = (minutes?: number) => !minutes ? '所选额度窗口' : minutes >= 1440 ? `${(minutes / 1440).toFixed(1).replace(/\.0$/, '')} 天额度窗口` : `${(minutes / 60).toFixed(1).replace(/\.0$/, '')} 小时额度窗口`;

export function BudgetStatus({ budget }: { budget?: BudgetSummary }) {
  if (budget?.mode==='observe_only') return <p>{budget.note}</p>;
  if (!budget || budget.mode === 'tokens') return <p>这份旧工单沿用原 token 预算；可以用百分比批准追加。</p>;
  if (budget.status === 'uncalibrated') return <p>额度目标 {budget.target_percent ?? 10}% · 当前模型待校准，暂不显示虚构的消耗百分比。</p>;
  if (budget.idle_guarded && budget.starts_at) return <p>本次 {budget.additional_percent}% 已获批 · 等桌面 Codex 空闲后开始校准；其他桌面任务介入会暂停并重新取样，不追加额度。剩余工作时限 {Math.ceil((budget.remaining_seconds ?? 1800)/60)} 分钟。</p>;
  if ((budget as BudgetSummary & {starts_at?:number}).starts_at) return <p>追加 {budget.additional_percent}% 已获批 · 即将开始独立校准。开工时会重新读取官方窗口起点，此前开发和并行消耗不纳入样本。</p>;
  if (budget.mode === 'calibrating') return <p>正在累计校准 · 本次最多追加 {budget.additional_percent}%（{windowLabel(budget.window_mins)}）· 已完成 {budget.completed_turns ?? 0} 个真实回合{budget.deadline ? ` · 校准到 ${new Date(budget.deadline * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}。样本不足不会打断正常交付。</p>;
  return <p>工单估算已用 <strong>{budget.used_percent}%</strong> / 上限 {budget.limit_percent}% · {windowLabel(budget.window_mins)} · {budget.sample_count} 个独立参考样本。桌面消耗不计入此工单。</p>;
}

export default function BudgetApproval({ job, reference, busy, onApprove }: {
  job: Job; reference?: BudgetReference | null; busy: boolean;
  onApprove: (body: { additional_percent: number; calibration_exclusive: boolean; mode: 'continue' | 'discuss' }) => void;
}) {
  const [addition, setAddition] = useState(5);
  const [exclusive, setExclusive] = useState(false);
  const calibrated = Boolean(reference || job.budget_summary?.status === 'estimated');
  const valid = Number.isFinite(addition) && addition >= .1 && addition <= 100;
  const blocked = busy || !valid || (!calibrated && !exclusive) || job.usage_known === 0;
  const reasons: Record<string, string> = { token_budget: '已触碰本工单预算', usage_unknown: '线程用量尚未确认，需要先核对现场', calibration_required: '当前模型尚无有效百分比参考', calibration_reset: '校准跨过额度重置，需要重新取样', calibration_unavailable: '本次校准额度回执不可用', calibration_expired: '最长 30 分钟的校准阶段已结束', calibration_limit: '已触碰本次批准的校准额度增量' };
  return <article className="workshop-budget-approval">
    <h3>{job.title} · 预算暂停</h3>
    <BudgetStatus budget={job.budget_summary} />
    <p>{job.visibility === 'private' ? '保密内容仍由 AI 决定是否公开。' : reasons[job.reason] || job.reason}</p>
    <label>本次追加额度（估算百分点）<input type="number" min="0.1" max="100" step="0.1" value={addition} onChange={event => setAddition(Number(event.target.value))} /></label>
    {!calibrated && <>
      <p>首次批准会同时校准：最长 30 分钟内累计这份实际工单的 token 和官方长额度窗口回执，不额外空跑模型。校准期间需暂停同账号其他 Codex 的生成。</p>
      <label><input type="checkbox" checked={exclusive} onChange={event => setExclusive(event.target.checked)} />校准期间，我暂停同账号桌面及其他 Codex 的模型调用</label>
      <p>窗口变化暂不足 1 个百分点时继续累计，正常方案确认和作品验收不暂停。校准时限、获批增量、账号门槛或回执条件触碰边界后才停；已有成果保留。</p>
    </>}
    <button type="button" disabled={blocked} onClick={() => onApprove({ additional_percent: addition, calibration_exclusive: exclusive, mode: 'continue' })}>{calibrated ? '批准追加并继续排队' : '批准追加并校准继续'}</button>
    <button type="button" disabled={blocked} onClick={() => onApprove({ additional_percent: addition, calibration_exclusive: exclusive, mode: 'discuss' })}>批准讨论额度 · 重新确定方向</button>
    <p>从当前累计量或已有上限中较高者追加；保留已消耗量。账号剩余 30% 等准入门槛继续生效；用量分批返回，可能超过门槛后才暂停。</p>
    <details><summary>用量原始记录</summary><p>{job.tokens.toLocaleString()} / {job.token_cap.toLocaleString()} tokens</p>{!calibrated && <p>首次校准另有 1000 万 tokens 的紧急上限及单回合时限，这不是百分比换算值。</p>}</details>
  </article>;
}
