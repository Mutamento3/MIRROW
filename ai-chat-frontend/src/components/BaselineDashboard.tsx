/** 基线仪表板 — AI 对人类伙伴身体节律的了解程度 */

interface Props {
  baseline: any;
  todaySteps: number;
  todayHr: number | null;
}

const DIM_LABELS: Record<string, { icon: string; label: string; unit: string }> = {
  steps: { icon: '🚶', label: '步数', unit: '步/天' },
  heart_rate: { icon: '💓', label: '静息心率', unit: 'bpm' },
  sleep: { icon: '😴', label: '睡眠', unit: 'h' },
  pc_active: { icon: '💻', label: 'PC活跃', unit: 'h/天' },
  reply_interval: { icon: '⏱️', label: '回复间隔', unit: 's' },
  outing: { icon: '🏃', label: '外出步数', unit: '步' },
  push_feedback: { icon: '📨', label: '推送反馈', unit: '%' },
};

const TREND_EMOJI: Record<string, string> = {
  rising: '↗', falling: '↘', stable: '→', uncertain: '~',
};

export default function BaselineDashboard({ baseline, todaySteps, todayHr }: Props) {
  if (!baseline) {
    return <div style={{ textAlign: 'center', padding: 20, color: '#888' }}>基线数据加载中...</div>;
  }

  const { confidence, cold_start, dimensions, trend_signals, drift } = baseline;
  const dims = dimensions || {};
  const trends = trend_signals || {};
  const driftDims = drift?.dimensions || {};

  const pct = Math.round((confidence || 0) * 100);

  return (
    <div>
      {/* 置信度条 */}
      <div style={{
        padding: '6px 10px', borderRadius: 6, marginBottom: 10,
        background: cold_start ? 'rgba(245,158,11,0.1)' : 'rgba(34,197,94,0.08)',
        fontSize: '0.85em', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span>
          {cold_start ? '🧪' : '✅'} 置信度: {pct}% · 样本: ~{Math.round(pct * 0.18)}天
        </span>
        <span style={{ color: cold_start ? '#f59e0b' : '#22c55e', fontSize: '0.8em' }}>
          {cold_start ? '学习中…' : '就绪'}
        </span>
      </div>

      {/* 7维度卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
        {Object.entries(DIM_LABELS).map(([key, meta]) => {
          const dim = dims[key];
          if (!dim || !dim.available) return null;
          const trend = trends[key];
          const driftDim = driftDims[key];

          // 从摘要字符串中提取数值（如 "工作日均 6441步" → 6441）
          const extractNum = (s: any): number => {
            if (typeof s === 'number') return s;
            if (typeof s !== 'string') return NaN;
            const m = s.match(/[\d.]+/);
            return m ? parseFloat(m[0]) : NaN;
          };
          const baselineVal = extractNum(dim.summary);
          let currentVal: number | null = null;
          let currentPct: number | null = null;
          if (key === 'steps' && todaySteps > 0) {
            currentVal = todaySteps;
            currentPct = !isNaN(baselineVal) && baselineVal > 0 ? Math.round(todaySteps / baselineVal * 100) : null;
          } else if (key === 'heart_rate' && todayHr) {
            currentVal = todayHr;
            currentPct = !isNaN(baselineVal) && baselineVal > 0 ? Math.round(todayHr / baselineVal * 100) : null;
          }

          const pctColor = currentPct !== null
            ? (currentPct < 30 ? '#ef4444' : currentPct < 60 ? '#f59e0b' : currentPct > 150 ? '#3b82f6' : '#22c55e')
            : '#888';

          return (
            <div key={key} style={{
              padding: '8px 10px', borderRadius: 6, fontSize: '0.85em',
              background: driftDim ? 'rgba(239,68,68,0.06)' : 'rgba(0,0,0,0.03)',
              border: driftDim ? '1px solid rgba(239,68,68,0.15)' : 'none',
            }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                {meta.icon} {meta.label}
                {driftDim && <span style={{ color: '#ef4444', fontSize: '0.75em', marginLeft: 4 }}>⚠漂移</span>}
              </div>
              <div style={{ color: '#333' }}>
                基线: <b>{dim.summary}</b> {meta.unit}
              </div>
              {currentVal !== null && (
                <div style={{ marginTop: 2, color: pctColor }}>
                  当前: <b>{currentVal}</b>
                  {currentPct !== null && <span> ({currentPct}%)</span>}
                </div>
              )}
              {trend && (
                <div style={{ fontSize: '0.8em', marginTop: 2, color: '#888' }}>
                  {TREND_EMOJI[trend.slice(0, 1) === '↗' ? 'rising' : trend.slice(0, 1) === '↘' ? 'falling' : 'stable']} {trend}
                </div>
              )}
              {currentVal === null && !trend && (
                <div style={{ fontSize: '0.75em', color: '#aaa', marginTop: 2 }}>暂无今日数据</div>
              )}
            </div>
          );
        })}
      </div>

      {/* 漂移告警 */}
      {Object.keys(driftDims).length > 0 && (
        <div style={{
          marginTop: 10, padding: '6px 10px', borderRadius: 6,
          background: 'rgba(239,68,68,0.08)', fontSize: '0.82em', color: '#b91c1c',
        }}>
          ⚠️ 基线漂移: {Object.entries(driftDims).map(([k, v]: [string, any]) => (
            `${DIM_LABELS[k]?.label || k} 持续偏离(${v?.days || '?'}天)`
          )).join(' · ')}
        </div>
      )}

      {Object.keys(dims).length === 0 && (
        <div style={{ textAlign: 'center', padding: 20, color: '#888' }}>
          🧪 基线学习中，请等待至少 3 天数据积累
        </div>
      )}
    </div>
  );
}
