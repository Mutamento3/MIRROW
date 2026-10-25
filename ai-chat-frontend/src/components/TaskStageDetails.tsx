import './TaskStageDetails.css';

type Stage = { name: string; label: string; status: string; reused?: boolean };
const STATUS: Record<string, string> = {
  pending: '等待', running: '进行中', completed: '完成', cached: '已是最新',
  completed_with_rejections: '完成，部分待核实', no_evidence: '无材料',
  not_triggered: '未触发', error: '失败', interrupted: '已中断', not_started: '未执行',
};

export function TaskStageDetails({ stages }: { stages?: Stage[] }) {
  if (!stages?.length) return null;
  const active = stages.find(stage => stage.status === 'running');
  return <details className="task-stage-details">
    <summary>{active ? `正在整理：${active.label}` : '查看各项状态'}</summary>
    <ul>{stages.map(stage => <li key={stage.name} data-status={stage.status}>
      <span>{stage.label}</span>
      <span>{stage.reused ? '沿用已完成结果' : STATUS[stage.status] || stage.status}</span>
    </li>)}</ul>
  </details>;
}
