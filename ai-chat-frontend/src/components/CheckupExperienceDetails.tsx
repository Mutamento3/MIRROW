import type { ToolCallRecord } from '../types';

type Observation = {
  kind?: string;
  version?: number;
  source_node_id?: string;
  recorded_reflection?: string;
  reflection_is_fact_projection?: boolean;
  reflection_is_retained?: boolean;
  [key: string]: unknown;
};

// Reads the same frozen experience used by context and daily sources. There
// is no new request, summary model or independently editable feeling store.
export default function CheckupExperienceDetails({ toolCall }: { toolCall: ToolCallRecord }) {
  const frozen = toolCall.extra_data?.memory_experience;
  const observations: Observation[] = Array.isArray(frozen?.materials)
    ? frozen.materials.filter((item: Observation) => item.kind === 'checkup' && item.version === 1) : [];
  const feelings = [...new Set<string>([
    ...observations.filter(item => !item.reflection_is_fact_projection && !item.reflection_is_retained)
      .map(item => item.recorded_reflection || ''),
    typeof frozen?.retained_impression === 'string' ? frozen.retained_impression : '',
    typeof frozen?.settlement_reflection === 'string' ? frozen.settlement_reflection : '',
  ].filter(Boolean))];
  return <div className="tool-call-result-content checkup-experience-details">
    <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      {feelings.length ? feelings.map((text, index) => <p key={index}>{text}</p>)
        : <p>这次没有留下独立的原述感想。</p>}
    </div>
    <details>
      <summary>查看本次观察来源 · {observations.length} 个节点</summary>
      <p style={{ fontSize: '0.85em', opacity: 0.75 }}>上方是 AI 当时的感想；取得了什么、哪里未知，以以下回执为准。</p>
      {observations.map((item, index) => {
        const { recorded_reflection: _account, reflection_is_fact_projection: _derived,
          reflection_is_retained: _retained, ...receipt } = item;
        return <pre key={item.source_node_id || index} style={{
          whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.85em', maxHeight: 400, overflowY: 'auto',
        }}>{JSON.stringify(receipt, null, 2)}</pre>;
      })}
    </details>
  </div>;
}
