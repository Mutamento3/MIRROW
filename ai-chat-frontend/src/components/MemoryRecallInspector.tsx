import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { getApiBase } from '../config';
import './MemoryRecallInspector.css';

type RecallCandidate = {
  rank: number;
  decision: 'selected' | 'eliminated' | string;
  reason: string;
  summary_selected?: boolean;
  evidence_selected?: boolean;
  retrieval_decision?: string;
  source_layer: string;
  document_id: string;
  event_ids?: string[];
  source_message_ids?: string[];
  omitted_source_message_id_count?: number;
  source_anchors?: Array<{ message_id: string; span_start?: number | null; span_end?: number | null }>;
  summaries?: string[];
  display_text?: string;
  date_from?: string;
  date_to?: string;
  score?: number;
  lexical_score?: number;
  fuzzy_score?: number;
  semantic_score?: number | null;
  matched_terms?: string[];
};

type EvidenceMessage = {
  message_id: string;
  timestamp: string;
  role: string;
  content: string;
  is_anchor: boolean;
  content_truncated?: boolean;
  span_selected?: boolean;
  span_fallback?: boolean;
};

type RecallTrace = {
  trace_id: string;
  created_at: string;
  mode: string;
  status: string;
  reference_date: string;
  pipeline_version: string;
  index_version: string;
  projection_version: string;
  metrics: Record<string, unknown>;
  routing: Record<string, unknown>;
  candidates: RecallCandidate[];
  evidence: {
    expanded?: boolean;
    reason?: string;
    groups?: Array<{ rank: number; messages: EvidenceMessage[] }>;
  };
  expected_projection_text: string;
  actual_sections: Array<{ name: string; status: string; text: string; chars: number; tokens: number }>;
  actual_injected_text: string;
  active_searches?: Array<{
    attempt_id: string;
    ordinal: number;
    created_at: string;
    include_source_detail: boolean;
    requested_limit: number;
    reused: boolean;
    status: string;
    metrics: Record<string, unknown>;
    projection_text: string;
  }>;
  feedback: Array<{ feedback_id: string; label: string; note: string; created_at: string }>;
};

const SOURCE_LABELS: Record<string, string> = {
  event: '事件摘要',
  conversation: '原始对话窗',
  cognition: '认知记录',
  period: '时期视图',
};

const REASON_LABELS: Record<string, string> = {
  selected_top_k: '进入本轮有界结果',
  selected_summary: '作为摘要进入预计投影',
  selected_evidence: '作为原文证据进入预计投影',
  selected_summary_and_evidence: '摘要与原文证据均进入预计投影',
  overlapping_source_evidence: '与更高位来源重叠',
  raw_reserved_for_source_fallback: '已有摘要，原始对话仅保留作兜底',
  raw_only_source_fallback: '没有摘要，改用原始对话来源兜底',
  source_layer_limit: '该来源层已达到本轮名额',
  projection_limit: '超过本轮投影名额',
  output_limit: '超过本轮输出名额',
  cognition_entity_not_in_scope: '当前问句没有提到这位人物',
  cognition_lane_not_relevant: '与当前问题的关联不足，未强行带入认知',
  insufficient_query_support: '与当前问题缺少足够关联，未带入',
  tentative_recall_budget: '仅为弱关联，本轮已保留更相关的一条线索',
};

const EVIDENCE_REASON_LABELS: Record<string, string> = {
  no_hits: '没有命中',
  direct_summary_sufficient: '摘要直接命中，未展开原文',
  summary_sufficient: '已有相关摘要，原文保持按需',
  explicit_detail: '问句要求细节，已展开原文',
  raw_source_candidate: '原始对话直接命中',
  raw_only_fallback: '没有可用摘要，已用原始对话兜底',
  source_unavailable: '相关摘要没有可展开的原始消息',
  indirect_top_hit: '顶部命中较间接，已核对原文',
};

const STATUS_LABELS: Record<string, string> = {
  disabled: 'Memory V2 影子召回尚未启用',
  not_configured: 'Memory V2 影子库尚未配置',
  warming: 'Memory V2 索引当时仍在预热',
  no_query: '这一轮没有检索线索，未执行自动召回',
  error: 'Memory V2 影子召回当时失败',
};

function statusLabel(trace: RecallTrace): string {
  if (trace.status === 'observed') {
    return trace.mode === 'enabled'
      ? '召回已完成；实际注入见右栏'
      : '影子召回已完成，结果未注入';
  }
  return STATUS_LABELS[trace.status] || trace.status;
}

const FEEDBACKS = [
  ['accurate', '准确'],
  ['missed', '没想起来'],
  ['wrong_or_crossed', '想错／串错'],
  ['too_much', '带得太多'],
] as const;

function summaryFor(candidate: RecallCandidate): string {
  const display = String(candidate.display_text || '').trim();
  if (display) return display;
  const summaries = Array.isArray(candidate.summaries) ? candidate.summaries.filter(Boolean) : [];
  return summaries.join('；') || '这条候选没有可展示摘要。';
}

function score(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}

export default function MemoryRecallInspector({
  assistantMessageId,
  onClose,
}: {
  assistantMessageId: string;
  onClose: () => void;
}) {
  const [trace, setTrace] = useState<RecallTrace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState('');
  const [feedbackStatus, setFeedbackStatus] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetch(`${getApiBase()}/api/memory-v2/traces/by-assistant/${encodeURIComponent(assistantMessageId)}`, {
      signal: controller.signal,
    })
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || '读取记忆召回记录失败');
        return body;
      })
      .then(body => setTrace(body.trace || null))
      .catch(fetchError => {
        if (fetchError?.name !== 'AbortError') {
          setError(fetchError instanceof Error ? fetchError.message : '读取记忆召回记录失败');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [assistantMessageId]);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onEscape);
    return () => window.removeEventListener('keydown', onEscape);
  }, [onClose]);

  const submitFeedback = async (label: string) => {
    if (!trace || submitting) return;
    setSubmitting(label);
    setFeedbackStatus('');
    try {
      const response = await fetch(`${getApiBase()}/api/memory-v2/traces/${encodeURIComponent(trace.trace_id)}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label, note: note.trim() }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '反馈保存失败');
      setTrace(previous => previous ? {
        ...previous,
        feedback: [...previous.feedback, body.feedback],
      } : previous);
      setNote('');
      setFeedbackStatus('已记录，只用于离线评测。');
    } catch (submitError) {
      setFeedbackStatus(submitError instanceof Error ? submitError.message : '反馈保存失败');
    } finally {
      setSubmitting('');
    }
  };

  const groups = trace?.evidence?.groups || [];
  const latestFeedback = trace?.feedback?.length ? trace.feedback[trace.feedback.length - 1] : null;
  const activeSearches = trace?.active_searches || [];
  const queryEnvelope = trace?.routing?.query_envelope as Record<string, unknown> | undefined;

  return createPortal(
    <div className="memory-recall-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="memory-recall-card" role="dialog" aria-modal="true" aria-label="这条 AI 消息的记忆召回" onMouseDown={event => event.stopPropagation()}>
        <header className="memory-recall-header">
          <div>
            <h3>记忆召回</h3>
            <p>这条 AI 消息生成时的有界记忆链</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>

        {loading && <div className="memory-recall-state">正在读取本地记录…</div>}
        {!loading && error && <div className="memory-recall-state error">{error}</div>}

        {!loading && trace && (
          <div className="memory-recall-body">
            <div className="memory-recall-meta">
              <span>{trace.reference_date || '日期未记录'}</span>
              <span>候选 {String(trace.metrics?.candidate_count ?? '—')}</span>
              <span>精排 {String(trace.metrics?.evaluated_count ?? '—')}</span>
              <span>返回 {String(trace.metrics?.returned_count ?? trace.candidates.filter(item => item.decision === 'selected').length)}</span>
            </div>

            <section className="memory-recall-column shadow">
              <div className="memory-recall-section-title">
                <div><h4>{trace.mode === 'enabled' ? '召回候选' : '影子召回'}</h4><span>{statusLabel(trace)}</span></div>
                <code>{trace.pipeline_version}<br />{trace.index_version}<br />{trace.projection_version}</code>
              </div>

              <div className="memory-recall-routing">
                <span>路由：{trace.routing?.needs_history ? '需要历史' : '未判定需要历史'}</span>
                <span>{trace.routing?.needs_source_detail ? '需要原文细节' : '摘要优先'}</span>
                <span>排序 {String(trace.routing?.sort_mode || '—')}</span>
                {Boolean(trace.routing?.date_from || trace.routing?.date_to) && <span>日期 {String(trace.routing?.date_from || '…')} → {String(trace.routing?.date_to || '…')}</span>}
                {Boolean(queryEnvelope?.used_recent_context) && <span>指代补全：读取近期普通对话 {String(queryEnvelope?.recent_message_count || 0)} 条</span>}
                <span>planner 未启用</span>
              </div>
              {Boolean(trace.metrics?.candidate_counts_by_layer) && (
                <div className="memory-recall-source-counts">
                  {Object.entries(trace.metrics.candidate_counts_by_layer as Record<string, unknown>).map(([layer, count]) => (
                    <span key={layer}>{SOURCE_LABELS[layer] || layer} {String(count)} → 精排 {String((trace.metrics.evaluated_counts_by_layer as Record<string, unknown> | undefined)?.[layer] ?? '—')}</span>
                  ))}
                </div>
              )}

              <div className="memory-recall-candidates">
                {trace.candidates.length === 0 && <div className="memory-recall-empty">本轮没有检索到候选。</div>}
                {trace.candidates.map(candidate => (
                  <details className={`memory-recall-candidate ${candidate.decision}`} key={`${candidate.source_layer}:${candidate.document_id}`} open={candidate.decision === 'selected'}>
                    <summary>
                      <span className="memory-recall-rank">#{candidate.rank}</span>
                      <span className="memory-recall-source">{SOURCE_LABELS[candidate.source_layer] || candidate.source_layer}</span>
                      <span className={`memory-recall-decision ${candidate.decision}`}>{candidate.decision === 'selected' ? '选中' : '淘汰'}</span>
                      <span className="memory-recall-score">{score(candidate.score)}</span>
                    </summary>
                    <div className="memory-recall-candidate-body">
                      <p>{summaryFor(candidate)}</p>
                      <dl>
                        <div><dt>原因</dt><dd>{REASON_LABELS[candidate.reason] || candidate.reason}</dd></div>
                        <div><dt>进入位置</dt><dd>摘要 {candidate.summary_selected ? '是' : '否'} · 原文证据 {candidate.evidence_selected ? '是' : '否'}</dd></div>
                        <div><dt>日期</dt><dd>{candidate.date_from || '未标注'}{candidate.date_to && candidate.date_to !== candidate.date_from ? ` → ${candidate.date_to}` : ''}</dd></div>
                        <div><dt>评分</dt><dd>词法 {score(candidate.lexical_score)} · 模糊 {score(candidate.fuzzy_score)} · 语义 {score(candidate.semantic_score)}</dd></div>
                        <div><dt>命中词</dt><dd>{candidate.matched_terms?.join('、') || '无显式词项'}</dd></div>
                        <div><dt>原文来源</dt><dd>{candidate.source_message_ids?.length ? `${candidate.source_message_ids.length + (candidate.omitted_source_message_id_count || 0)} 条可追溯消息` : '没有原始消息锚点'}</dd></div>
                        {Boolean(candidate.source_message_ids?.length) && (
                          <div><dt>技术定位</dt><dd><details className="memory-source-ids"><summary>查看消息 ID</summary><code>{candidate.source_message_ids?.join('、')}{candidate.omitted_source_message_id_count ? `（另有 ${candidate.omitted_source_message_id_count} 个未在诊断卡复制）` : ''}</code></details></dd></div>
                        )}
                      </dl>
                    </div>
                  </details>
                ))}
              </div>

              <div className="memory-recall-evidence">
                <h5>原文展开</h5>
                <p>{EVIDENCE_REASON_LABELS[String(trace.evidence?.reason || '')] || trace.evidence?.reason || '未记录展开判断'}</p>
                {groups.flatMap(group => group.messages || []).map(message => (
                  <div className={`memory-evidence-message ${message.is_anchor ? 'anchor' : ''}`} key={`${message.message_id}:${message.timestamp}`}>
                    <span>{message.role === 'user' ? '人类伙伴' : 'AI'} · {message.timestamp}</span>
                    <p>{message.content}</p>
                    <small>{message.is_anchor ? '命中锚点' : '邻近原文'}{message.span_selected ? ' · 精确摘录' : ''}{message.span_fallback ? ' · span 失配后回退整条消息' : ''}{message.content_truncated ? ' · 已触及单条上限' : ''}</small>
                  </div>
                ))}
              </div>

              <details className="memory-recall-raw-block">
                <summary>查看 Memory V2 预计投影</summary>
                <pre>{trace.expected_projection_text || '本轮没有预计注入文本。'}</pre>
              </details>

              {activeSearches.length > 0 && (
                <div className="memory-recall-evidence">
                  <h5>本轮主动深搜</h5>
                  {activeSearches.map(search => (
                    <details className="memory-recall-raw-block" key={search.attempt_id} open>
                      <summary>
                        第 {search.ordinal} 次 · {search.reused ? '复用同轮召回' : '重新检索'} · {search.status}
                      </summary>
                      <small>{search.include_source_detail ? '包含原文核对' : '摘要优先'} · 上限 {search.requested_limit} 条</small>
                      <pre>{search.projection_text || '这次没有找到可呈现的记忆。'}</pre>
                    </details>
                  ))}
                </div>
              )}
            </section>

            <section className="memory-recall-column actual">
              <div className="memory-recall-section-title">
                <div><h4>实际注入</h4><span>AI 在这轮真正收到的全部记忆相关段</span></div>
                <code>Context Builder snapshot</code>
              </div>
              {trace.actual_sections.length === 0 ? (
                <div className="memory-recall-empty">本轮没有实际注入记忆材料。</div>
              ) : trace.actual_sections.map(section => (
                <details className="memory-actual-section" key={section.name} open>
                  <summary><span>{section.name}</span><small>{section.chars} 字符 · 约 {section.tokens} tokens</small></summary>
                  <pre>{section.text}</pre>
                </details>
              ))}
            </section>

            <section className="memory-recall-feedback">
              <div>
                <h4>这次记忆表现怎么样？</h4>
                <p>{latestFeedback ? `最近反馈：${FEEDBACKS.find(item => item[0] === latestFeedback.label)?.[1] || latestFeedback.label}` : '反馈只进入离线评测，不会自动修改任何记忆。'}</p>
              </div>
              <textarea value={note} onChange={event => setNote(event.target.value)} maxLength={1000} placeholder="可选：补一句具体哪里对／哪里错" />
              <div className="memory-recall-feedback-actions">
                {FEEDBACKS.map(([label, text]) => (
                  <button type="button" key={label} disabled={Boolean(submitting)} onClick={() => submitFeedback(label)}>
                    {submitting === label ? '保存中…' : text}
                  </button>
                ))}
              </div>
              {feedbackStatus && <div className="memory-recall-feedback-status" role="status">{feedbackStatus}</div>}
            </section>

            <footer>“完整”仅指这一轮有界记忆链；不包含完整系统提示、非记忆上下文、凭据或隐藏推理。</footer>
          </div>
        )}
      </section>
    </div>,
    document.body,
  );
}
