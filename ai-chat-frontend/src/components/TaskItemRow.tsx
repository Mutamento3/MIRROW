/** 单条任务行卡片（折叠式） */
import { cronToChinese } from '../utils';
import type { TaskItem } from '../types';

export function TaskItemRow({ task, onToggle, onDelete, isCheckup, isExpanded, onToggleExpand }: {
  task: TaskItem; onToggle: (t: TaskItem) => void | Promise<void>; onDelete: (id: string) => void | Promise<void>; isCheckup?: boolean;
  isExpanded: boolean; onToggleExpand: () => void;
}) {
  const isSelfTask = task.task_type === 'self_task';
  const isInconsistency = task.task_type === 'inconsistency';
  const creatorEmoji = task.created_by === 'ai' ? '🤖' : '👤';
  const typeBadge = isSelfTask ? '🤖' : isInconsistency ? '🔍' : creatorEmoji;
  return (
    <div className={`task-item${!task.is_active ? ' task-inactive' : ''}${isCheckup ? ' task-checkup' : ''}`}>
      {/* 折叠栏 */}
      <div className="task-item-bar" onClick={onToggleExpand}>
        <span className="task-item-bar-emoji">{typeBadge}</span>
        <span className="task-item-bar-title">{task.title}</span>
        {!task.is_active && <span className="ta">(已失效)</span>}
        <span className="task-item-bar-arrow">{isExpanded ? '△' : '▽'}</span>
      </div>

      {/* 展开详情 */}
      {isExpanded && (
        <div className="task-item-detail">
          {task.reason && <div className="task-reason">💡 {task.reason}</div>}
          {task.execution_prompt && <div className="task-ai-context">🎯 {task.execution_prompt}</div>}
          <div className="task-meta">
            <span className="task-trigger">
              {task.trigger_type === 'cron'
                ? `⏱ ${cronToChinese(task.trigger_config.cron || '')}`
                : `📅 ${task.trigger_config.datetime || '-'}`}
            </span>
            {!isCheckup && (
              <span className="task-method">
                {task.notify_method.map(m => m === 'popup' ? '🔔弹窗' : '💬消息').join(' ')}
                {task.alarm_enabled ? ' ⏰手机闹钟' : ''}
              </span>
            )}
            {task.is_ai_generated && <span className="task-ai-badge">AI</span>}
            {isSelfTask && task.tool_name && <span className="task-checkup-tool" title="将调用的工具">🛠️ {task.tool_name}</span>}
            {isInconsistency && <span className="task-checkup-tool" title="系统查岗">🛠️ 查岗</span>}
            <span className="task-creator">{task.created_by === 'ai' ? '🤖 AI创建' : '👤 人类伙伴创建'}</span>
          </div>
          {task.last_triggered_at && !isSelfTask && (
            <div className="task-last-triggered">上次触发: {new Date(task.last_triggered_at).toLocaleString()}</div>
          )}
          <div className="task-item-actions">
            {!isCheckup && (
              <label className="task-toggle-label">
                <input type="checkbox" checked={task.is_active} onChange={() => onToggle(task)} />
                <span>{task.is_active ? '已启用' : '已停用'}</span>
              </label>
            )}
            <button className="task-delete-btn" onClick={(e) => { e.stopPropagation(); onDelete(task.id); }}
              title={isSelfTask ? '取消自动任务' : '删除'}>× 删除</button>
          </div>
        </div>
      )}
    </div>
  );
}
