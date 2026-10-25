import WorkshopGiftCard, { type GiftCard } from '../workshop/WorkshopGiftCard';
import { BudgetStatus } from '../workshop/WorkshopBudget';
import type { BudgetSummary } from '../workshop/types';
type Card = Partial<GiftCard> & { source?: string; invitation_message_id?: string; budget_summary?: BudgetSummary; visibility?: 'public' | 'private'; job_id: string; title: string; kind: string; timestamp?: string; revision?: number; tokens?: number; token_cap?: number; summary?: string; reason?: string; plan?: string; brief?: string };
export default function WorkshopEventCard({ card, onOpen }: { card: Card; onOpen: (id: string) => void }) {
  if (card.kind === 'artifact_shared' && card.visibility === 'public' && card.artifacts?.length) return <WorkshopGiftCard card={card as GiftCard} onOpen={onOpen} />;
  // The confirmed shortcut already has a user message. Keep its lifecycle in
  // backend history while failures and actual work-order progress stay visible.
  if (card.source === 'ui_action' && card.invitation_message_id &&
      ['visit_started', 'visit_finished'].includes(card.kind)) return null;
  if (card.kind === 'cabinet_visited') return <div className="lounge-event-row"><details className="social-gift-event"><summary className="lounge-event-trigger"><span className="lounge-event-icon" aria-hidden="true">👣</span><span className="lounge-event-copy">作品柜 · 人类伙伴来参观了</span></summary><p>{card.summary}</p><button onClick={() => onOpen('')}>打开工作坊</button></details></div>;
  if (card.kind.startsWith('visit_')) {
    const labels: Record<string, string> = { visit_started: '来访中', visit_finished: '来访结束', visit_failed: '来访未完成', visit_interrupted: '来访中断' };
    return <div className="lounge-event-row"><details className="social-gift-event"><summary className="lounge-event-trigger"><span className="lounge-event-icon" aria-hidden="true">🛠️</span><span className="lounge-event-copy">工作坊 · {labels[card.kind] || '来访记录'}</span></summary><p>{card.timestamp}</p><p>{card.summary}</p><button onClick={() => onOpen(card.job_id || '')}>{card.job_id ? '查看工单房间与当前进度' : '打开工作坊'}</button></details></div>;
  }
  const names: Record<string, string> = { desktop_waiting:'等待桌面空闲', resource_approval: '人类伙伴批准资源', published: 'AI 已公开', inspiration_added: '灵感补充已记录', budget_calibration: '额度参考校准', accepted: '工单已接受', plan: '方案待确认', confirmed: 'AI 已确认方案', ready: '交付就绪 · 待独立质检', quality_queued:'Luna 质检排队', quality_report:'Luna 质检报告', quality_blocked:'Luna 等待资源', accepted_work: 'AI 已验收', budget_paused: '预算暂停', paused: '已暂停', failed: '执行待核对', recovery_required: '重启现场待核对', modified: '新灵感已记录', canceled: '已取消' };
  return <div className="lounge-event-row"><details className="social-gift-event"><summary className="lounge-event-trigger"><span className="lounge-event-icon" aria-hidden="true">🛠️</span><span className="lounge-event-copy">{card.title} · {names[card.kind] || card.kind}</span></summary><p>{card.timestamp}{card.revision ? ` · v${card.revision}` : ''}</p>{card.budget_summary ? <BudgetStatus budget={card.budget_summary} /> : card.tokens != null && <p>{card.tokens} / {card.token_cap} tokens</p>}<p>{card.summary || card.reason}</p>{card.brief && <p>工单原话节选：{card.brief.slice(0,600)}</p>}{card.plan && <p style={{whiteSpace:'pre-wrap'}}>{card.plan}</p>}{card.visibility === 'private' ? <p>🔒 内容由 AI 保密，公开后可查看。</p> : <button onClick={() => onOpen(card.job_id)}>查看工单房间与当前进度</button>}</details></div>;
}
