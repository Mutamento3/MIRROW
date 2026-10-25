import { useState } from 'react';
import { taskProgressTitle } from '../utils/taskProgressTitle';
import { TaskStageDetails } from './TaskStageDetails';
import './TaskProgressPanel.css';

type ProgressTask = {
  name?: string; label?: string; purpose?: string; status: string; stalled?: boolean;
  details?: { name: string; label: string; status: string; reused?: boolean }[];
};

export const TASK_LABELS: Record<string, string> = {
  memory_consolidation: '记忆整合', memory_v2_online_ingest: 'Memory V2 日总结',
  diary_backfill: '日记回填', daily_aggregation: '每日聚合', diary_generation: '日记生成',
  social_feed_summary: '共域收尾', unified_analysis: '统一分析', persona_evolution: '人格演化',
};

const COLLAPSED_KEY = 'mirrow.task-progress.collapsed';

export function TaskProgressPanel({ progress, onCancel }: {
  progress: { visible: boolean; tasks: Record<string, ProgressTask> | ProgressTask[]; allDone: boolean; progress: number };
  onCancel: (taskId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === 'true'; } catch { return false; }
  });
  if (!progress.visible) return null;
  const tasks = Object.entries(progress.tasks);
  const failed = tasks.some(([, task]) => task?.status === 'error');
  const title = taskProgressTitle(progress.tasks);
  const toggle = () => {
    setCollapsed(value => {
      try { localStorage.setItem(COLLAPSED_KEY, String(!value)); } catch { /* local preference only */ }
      return !value;
    });
  };
  return <section className={`task-progress-panel${collapsed ? ' is-collapsed' : ''}`} aria-label="整理任务进度">
    <div className="ta">
      <span className="ta" title={title}>{failed ? '⚠️ ' : '◷ '}{failed ? '整理有未完成项' : title}</span>
      <span className="ta">{progress.progress}%</span>
      <button type="button" className="ta" onClick={toggle}
        aria-expanded={!collapsed} aria-controls="ta"
        aria-label={collapsed ? '展开整理进度' : '缩小整理进度'}>{collapsed ? '展开' : '缩小'}</button>
    </div>
    {!collapsed && <div className="ta" id="ta">
      <div className="task-progress-track"><div className="task-progress-fill" style={{ width: `${progress.progress}%` }} /></div>
      <div className="task-progress-items">{tasks.map(([key, task]) => {
        if (!task) return null;
        const icon = task.status === 'completed' ? '✅' : task.status === 'running' ? '⏳' : task.status === 'error' ? '❌' : '○';
        return <div key={task.name || key}>
          <div className={`task-progress-item ${task.status}${task.stalled ? ' stalled' : ''}`}>
            <span className="task-progress-icon">{icon}</span>
            <span className="task-progress-label">{task.label || TASK_LABELS[task.name || key] || task.name || key}</span>
            {task.stalled && task.status === 'running' && <button type="button" className="task-stall-cancel"
              title="疑似卡死：打印日志并终止" onClick={() => onCancel(task.name || key)}>❌</button>}
          </div>
          <TaskStageDetails stages={task.details} />
        </div>;
      })}</div>
      {progress.allDone && <div className="task-progress-done">{failed ? '部分没整理完 ❌' : '整理完成 ✓'}</div>}
    </div>}
  </section>;
}
