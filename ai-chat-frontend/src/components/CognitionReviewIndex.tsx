type ReviewSummary = { id: string; status: string; source_date: string; proposal?: { domain: string }; plan: { name: string; after: string } };

export const reviewStatus: Record<string, string> = {
  pending: '待确认', applying: '保存中', applied: '已采用', auto_applied: '已自动更新',
  rejected: '未采用', observing: '继续观察', unchanged: '无变化',
  isolated: '待重整', superseded: '已续接解决', error: '待重整',
};

/** A stable card rail: selecting another record never expands the list vertically. */
export default function CognitionReviewIndex({ items, index, disabled, onSelect }: {
  items: ReviewSummary[]; index: number; disabled: boolean; onSelect: (index: number) => void;
}) {
  return <nav className="review-card-rail" aria-label="更新卡片">
    {items.map((item, position) => <button key={item.id} type="button"
      className="review-summary-card" aria-pressed={position === index} disabled={disabled}
      onClick={() => onSelect(position)}>
      <span className="review-card-meta"><span>{item.proposal?.domain==='world'&&item.status==='pending'?'继续观察':reviewStatus[item.status] || '更新记录'}</span><time>{item.source_date}</time></span>
      <strong>{item.plan.name}</strong>
      <span className="review-card-excerpt">{item.plan.after || '查看原文'}</span>
    </button>)}
  </nav>;
}
