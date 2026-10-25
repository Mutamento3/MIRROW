/** 神经元子Agent 监控面板 */
import { useState, useMemo } from 'react';
import { useNeuronStatus, useNeuronHistory } from '../hooks/useNeurons';

type FilterCategory = 'all' | 'cognitive' | 'reflex' | 'perception' | 'never' | 'with_recipe';

const CATEGORY_LABELS: Record<string, string> = {
  cognitive: '认知', reflex: '反射', perception: '感知',
};
const CATEGORY_COLORS: Record<string, string> = {
  cognitive: '#a855f7', reflex: '#06b6d4', perception: '#9ca3af',
};

function relativeTime(iso: string): string {
  if (!iso) return '从未执行';
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 5) return '刚刚';
  if (s < 60) return `${s}秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}小时前`;
  const d = Math.floor(h / 24);
  return `${d}天前`;
}

function statusDot(status: string): { color: string; label: string } {
  switch (status) {
    case 'ok': return { color: '#22c55e', label: 'ok' };
    case 'error': return { color: '#ef4444', label: 'error' };
    case 'skipped': return { color: '#f59e0b', label: 'skipped' };
    default: return { color: '#666', label: 'never' };
  }
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

interface NeuronCardProps {
  neuron: any;
  expanded: boolean;
  onToggle: () => void;
  history: any[];
  historyLoading: boolean;
}

function NeuronCard({ neuron, expanded, onToggle, history, historyLoading }: NeuronCardProps) {
  const { last_trace, execution_count, success_count } = neuron;
  const lastStatus = last_trace?.status || 'never_executed';
  const dot = statusDot(lastStatus);
  const failRate = execution_count > 0 ? ((execution_count - success_count) / execution_count * 100).toFixed(0) : '0';

  return (
    <div className={`neuron-card${expanded ? ' expanded' : ''}`} style={{ borderLeft: `3px solid ${dot.color}` }}>
      {/* ── Collapsed header ── */}
      <div className="neuron-card-header" onClick={onToggle}>
        <span className="neuron-status-dot" style={{ background: dot.color }} title={dot.label} />
        <span className="neuron-emoji">{neuron.emoji || '🧩'}</span>
        <span className="neuron-name">{neuron.name}</span>
        <span className="neuron-badge" style={{ background: CATEGORY_COLORS[neuron.category] || '#888' }}>
          {CATEGORY_LABELS[neuron.category] || neuron.category}
        </span>
        {neuron.is_llm && (
          <span className="neuron-badge neuron-model-badge">{neuron.llm_model}</span>
        )}
        <span className="neuron-time">{relativeTime(last_trace?.timestamp || '')}</span>
        {last_trace?.duration_ms > 0 && (
          <span className="neuron-duration">{formatMs(last_trace.duration_ms)}</span>
        )}
        <span className="neuron-count">{execution_count}次</span>
        <span className="neuron-chevron">{expanded ? '▲' : '▼'}</span>
      </div>

      {/* ── Recipe indicator ── */}
      <div className="neuron-recipe-row">
        {neuron.recipe ? (
          <span className="neuron-recipe-tag recipe-ok">📋 {neuron.recipe}</span>
        ) : neuron.is_llm ? (
          <span className="neuron-recipe-tag recipe-hard">硬构建</span>
        ) : (
          <span className="neuron-recipe-tag recipe-none">—</span>
        )}
        {neuron.consumer && <span className="neuron-consumer">→ {neuron.consumer}</span>}
      </div>

      {/* ── Expanded detail ── */}
      {expanded && (
        <div className="neuron-card-body">
          <div className="neuron-meta">
            <div className="neuron-meta-row">
              <span className="neuron-meta-label">触发:</span>
              <span>{neuron.trigger_desc || '—'}</span>
            </div>
            {neuron.description && (
              <div className="neuron-meta-row">
                <span className="neuron-meta-label">职责:</span>
                <span>{neuron.description}</span>
              </div>
            )}
            <div className="neuron-meta-row">
              <span className="neuron-meta-label">总执行:</span>
              <span>{execution_count}次 · 成功: {success_count} · 失败: {execution_count - success_count} ({failRate}%)</span>
            </div>
          </div>

          {/* 输入/思考/输出 三段折叠 */}
          {last_trace && (
            <div className="neuron-trace-sections">
              <TraceSection icon="📥" label="输入" content={last_trace.input_preview} full={last_trace.input_full} />
              <TraceSection icon="🧠" label="思考" content={last_trace.thinking_preview} full={last_trace.thinking_full}
                placeholder={neuron.is_llm ? undefined : '规则引擎，无思考过程'} />
              <TraceSection icon="📤" label="输出" content={last_trace.output_preview} full={last_trace.output_full} />
              {last_trace.error_message && (
                <div className="neuron-error-block">
                  <strong>❌ 错误:</strong> {last_trace.error_message}
                </div>
              )}
            </div>
          )}

          {/* 执行历史 */}
          {history.length > 0 && (
            <div className="neuron-history">
              <div className="neuron-history-title">⏱ 执行历史 (最近{history.length}次)</div>
              <div className="neuron-history-list">
                {history.map((t: any) => {
                  const hdot = statusDot(t.status);
                  return (
                    <div key={t.trace_id} className="neuron-history-item">
                      <span className="neuron-status-dot" style={{ background: hdot.color, width: 7, height: 7, flexShrink: 0 }} />
                      <span className="neuron-history-time">{new Date(t.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
                      <span className="neuron-history-status">{t.status === 'ok' ? '✅' : t.status === 'error' ? '❌' : '⏭️'}</span>
                      <span className="neuron-history-duration">{formatMs(t.duration_ms)}</span>
                      <span className="neuron-history-output" title={t.output_full}>{t.output_preview || '—'}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {historyLoading && <div className="neuron-history-loading">加载中...</div>}
        </div>
      )}
    </div>
  );
}

function TraceSection({ icon, label, content, full, placeholder }: {
  icon: string; label: string; content: string; full?: string; placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const display = content || placeholder;
  if (!display && !full) return null;

  return (
    <div className="neuron-trace-section">
      <div className="neuron-trace-header" onClick={() => setOpen(!open)}>
        <span>{open ? '▼' : '▶'} {icon} {label}</span>
        {content && <span className="neuron-trace-chars">({content.length}字符)</span>}
      </div>
      {open && (
        <pre className="neuron-trace-content">{full || content || placeholder}</pre>
      )}
    </div>
  );
}


export default function NeuronPanel() {
  const { neurons, stats, loading, error, refresh } = useNeuronStatus();
  const [filter, setFilter] = useState<FilterCategory>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const { traces: history, loading: historyLoading } = useNeuronHistory(expandedId);

  // 按子系统分组
  const grouped = useMemo(() => {
    const filtered = neurons.filter(n => {
      switch (filter) {
        case 'cognitive': return n.category === 'cognitive';
        case 'reflex': return n.category === 'reflex';
        case 'perception': return n.category === 'perception';
        case 'never': return n.execution_count === 0;
        case 'with_recipe': return !!n.recipe;
        default: return true;
      }
    });
    const groups: Record<string, typeof filtered> = {};
    for (const n of filtered) {
      const sub = n.subsystem || 'other';
      if (!groups[sub]) groups[sub] = [];
      groups[sub].push(n);
    }
    // 排序：认知型多的组在前
    return Object.entries(groups).sort((a, b) => {
      const aCog = a[1].filter(n => n.category === 'cognitive').length;
      const bCog = b[1].filter(n => n.category === 'cognitive').length;
      return bCog - aCog;
    });
  }, [neurons, filter]);

  return (
    <div className="neuron-panel">
      {/* ── Summary bar ── */}
      <div className="neuron-summary-bar">
        <button className="btn-secondary" onClick={refresh} disabled={loading} style={{ marginRight: 8 }}>
          {loading ? '刷新中...' : '🔄 刷新'}
        </button>
        {stats && (
          <>
            <span className="neuron-summary-stat">
              🧠 {stats.total}个神经元 · 认知{stats.cognitive} 反射{stats.reflex} 感知{stats.perception}
            </span>
            <span className="neuron-summary-stat" style={{ marginLeft: 12 }}>
              Recipe化 {stats.migration_pct}%
            </span>
            <span className="neuron-migration-bar" title={`${stats.with_recipe}/${stats.total} 已接入Recipe`}>
              <span className="neuron-migration-fill" style={{ width: `${stats.migration_pct}%` }} />
            </span>
            {stats.never_executed > 0 && (
              <span className="neuron-summary-stat" style={{ marginLeft: 12, color: '#f59e0b' }}>
                从未执行 {stats.never_executed}
              </span>
            )}
          </>
        )}
      </div>

      {/* ── Filter bar ── */}
      <div className="neuron-filter-bar">
        {([
          ['all', '全部'],
          ['cognitive', '认知'],
          ['reflex', '反射'],
          ['perception', '感知'],
          ['never', '从未执行'],
          ['with_recipe', '已接入Recipe'],
        ] as [FilterCategory, string][]).map(([key, label]) => (
          <button
            key={key}
            className={`neuron-filter-btn${filter === key ? ' active' : ''}`}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
        {error && <span className="neuron-error-text">⚠ {error}</span>}
      </div>

      {/* ── Neuron cards grouped by subsystem ── */}
      {loading && neurons.length === 0 ? (
        <div className="monitor-loading">
          <div className="typing-indicator"><span></span><span></span><span></span></div>
          加载神经元状态...
        </div>
      ) : (
        grouped.map(([subsystem, items]) => {
          const isCollapsed = collapsedGroups.has(subsystem);
          return (
            <div key={subsystem} className="neuron-group">
              <div
                className="neuron-group-header"
                onClick={() => setCollapsedGroups(prev => {
                  const next = new Set(prev);
                  if (next.has(subsystem)) next.delete(subsystem); else next.add(subsystem);
                  return next;
                })}
              >
                <span>{isCollapsed ? '▶' : '▼'} {subsystem}</span>
                <span className="neuron-group-count">({items.length})</span>
              </div>
              {!isCollapsed && (
                <div className="neuron-grid">
                  {items.map(n => (
                    <NeuronCard
                      key={n.neuron_id}
                      neuron={n}
                      expanded={expandedId === n.neuron_id}
                      onToggle={() => setExpandedId(expandedId === n.neuron_id ? null : n.neuron_id)}
                      history={expandedId === n.neuron_id ? history : []}
                      historyLoading={expandedId === n.neuron_id ? historyLoading : false}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
