/** 偏离时间线 — AI 注意到你今天和平时不太一样 */

interface Deviation {
  timestamp: string;
  dimension: string;
  current_value: number;
  baseline_mean: number;
  z_score: number;
  severity: string;
  deviation_direction: string;
  compound_group_id: string | null;
  first_seen_at: string | null;
  repeat_count: number;
  last_seen_at: string | null;
}

interface Props {
  data: { days: number; count: number; deviations: Deviation[]; compound_groups: Record<string, string[]> } | null;
}

const SEVERITY_COLORS: Record<string, string> = {
  mild: '#f59e0b',
  moderate: '#f97316',
  severe: '#ef4444',
};

const DIM_LABELS: Record<string, string> = {
  steps: '步数', heart_rate: '心率', sleep: '睡眠',
  pc_active: 'PC活跃', reply_interval: '回复间隔', outing: '外出步数', push_feedback: '推送反馈',
};

const DIR_LABELS: Record<string, string> = {
  high: '偏高', low: '偏低',
};

function fmtTs(ts: string) {
  try {
    const d = new Date(ts);
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getDate().toString().padStart(2, '0')} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
  } catch {
    return ts?.slice(5, 16) || ts;
  }
}

export default function DeviationTimeline({ data }: Props) {
  if (!data || data.count === 0) {
    return <div style={{ textAlign: 'center', padding: 20, color: '#888' }}>近 7 天无偏离记录 🎉</div>;
  }

  const { deviations, compound_groups } = data;

  // 按日期分组
  const byDate: Record<string, Deviation[]> = {};
  for (const d of deviations) {
    const date = d.timestamp?.slice(0, 10) || 'unknown';
    if (!byDate[date]) byDate[date] = [];
    byDate[date].push(d);
  }

  return (
    <div>
      {Object.entries(byDate).map(([date, items]) => (
        <div key={date} style={{ marginBottom: 12 }}>
          <div style={{
            fontSize: '0.8em', fontWeight: 600, color: '#666',
            marginBottom: 4, paddingBottom: 2, borderBottom: '1px solid rgba(0,0,0,0.06)',
          }}>
            {date}
          </div>
          {items.map((d, i) => {
            const color = SEVERITY_COLORS[d.severity] || '#888';
            const dimLabel = DIM_LABELS[d.dimension] || d.dimension;
            const dirLabel = DIR_LABELS[d.deviation_direction] || d.deviation_direction;
            const pct = d.baseline_mean > 0 ? Math.round((d.current_value - d.baseline_mean) / d.baseline_mean * 100) : 0;
            const hasCompound = d.compound_group_id && compound_groups?.[d.compound_group_id]?.length >= 2;

            return (
              <div key={i} style={{
                padding: '4px 8px', marginBottom: 3, borderRadius: 4, fontSize: '0.85em',
                borderLeft: `3px solid ${color}`,
                background: hasCompound ? 'rgba(239,68,68,0.04)' : 'transparent',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>
                    <span style={{ color, fontWeight: 600 }}>
                      {d.severity === 'severe' ? '🔴' : d.severity === 'moderate' ? '🟠' : '🟡'} {dimLabel}{dirLabel}
                    </span>
                    <span style={{ marginLeft: 6, color: '#666' }}>
                      {pct > 0 ? '+' : ''}{pct}% (z={d.z_score?.toFixed(1)})
                    </span>
                  </span>
                  <span style={{ color: '#888', fontSize: '0.8em' }}>{fmtTs(d.timestamp)}</span>
                </div>
                {d.repeat_count > 1 && (
                  <div style={{ fontSize: '0.75em', color: '#999', marginTop: 1 }}>
                    重复 {d.repeat_count} 次{hasCompound ? ` · 复合信号[${d.compound_group_id!.slice(0, 6)}]` : ''}
                  </div>
                )}
                {hasCompound && d.repeat_count <= 1 && (
                  <div style={{ fontSize: '0.75em', color: '#999', marginTop: 1 }}>
                    复合信号[{d.compound_group_id!.slice(0, 6)}]: {compound_groups![d.compound_group_id!].map(dim => DIM_LABELS[dim] || dim).join(', ')}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
