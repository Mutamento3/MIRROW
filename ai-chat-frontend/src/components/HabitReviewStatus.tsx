type Rejection = { code: string; count: number };
type Diagnostics = {
  extraction?: { rejections: Rejection[] };
  repair?: { status: string; requested_count: number; recovered_count: number; rejections: Rejection[] };
  review?: { status: string; deferred_count: number };
};
export type HabitRunStatus = {
  status: string;
  habit_review?: { completed: number; pending: number };
  habit_complete?: boolean;
  habit_diagnostics?: Diagnostics;
};

const reasons: Record<string, string> = {
  report_container_invalid: '报告列表无效', report_missing: '漏报',
  report_unknown_id: '报告编号不属于本轮', report_duplicate_id: '报告编号重复',
  outcome_invalid: '判断类型无效', reference_type: '引用格式无效',
  reference_range: '引用编号越界', required_references_missing: '缺少必要引用',
  reference_duplicate: '引用重复', reference_out_of_scope: '引用超出请求范围',
  baseline_target_mismatch: '目标与待复核认识不一致', target_invalid: '目标无效',
  claim_support_invalid: '逐认识证据合同未通过', claim_reference_mismatch: '认识与报告引用不一致',
  report_rejected: '报告未通过校验',
};
const repairLabels: Record<string, string> = {
  started: '已发起，尚未保存完整结果', completed: '已补齐有效报告，仍以审核结果为准',
  incomplete: '未补齐', failed: '失败', unknown: '状态待核查',
};

function Rejections({ label, rows }: { label: string; rows?: Rejection[] }) {
  if (!rows?.length) return null;
  return <p>{label}：{rows.map(row => `${reasons[row.code] || '报告未通过校验'} ${row.count} 项`).join('；')}。</p>;
}

export default function HabitReviewStatus({ run }: { run: HabitRunStatus }) {
  const counts = run.habit_review;
  const diagnostics = run.habit_diagnostics;
  if (!counts && !diagnostics) return null;
  const ended = ['completed', 'completed_with_rejections', 'cached', 'no_evidence'].includes(run.status);
  return <section aria-label="习惯复核状态">
    {counts && <p role="status">{counts.pending > 0
      ? `${ended ? '本轮整理结束，' : ''}习惯复核仍有 ${counts.pending} 项待处理；已完成 ${counts.completed} 项。`
      : `本轮习惯复核已完成 ${counts.completed} 项。`}</p>}
    {counts && counts.completed > 0 && <p>完成表示报告已处理，可能是材料不足，也可能只支持其中列出的认识；不代表整页习惯全部得到证实。</p>}
    {diagnostics && <details><summary>查看习惯复核诊断</summary>
      <Rejections label="初次提取" rows={diagnostics.extraction?.rejections} />
      {diagnostics.repair && <>
        <p>补报：{repairLabels[diagnostics.repair.status] || '状态待核查'}；
          本次取得 {diagnostics.repair.recovered_count} / {diagnostics.repair.requested_count} 份有效报告。</p>
        <Rejections label="补报校验" rows={diagnostics.repair.rejections} />
      </>}
      {diagnostics.review?.status === 'pending' && (counts?.pending === 0
        ? <p>本批报告已收尾；这里保留的是原自动审核未完成的记录，不代表自动审核已通过。</p>
        : <p>独立审核尚未完成{diagnostics.review.deferred_count > 0
          ? `，其中 ${diagnostics.review.deferred_count} 项本次未执行` : ''}。</p>)}
    </details>}
  </section>;
}
