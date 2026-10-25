import CalendarGrid from '../Calendar';
import { TaskItemRow } from './TaskItemRow';
import type { useCalendar } from '../hooks/useCalendar';

// 日程管理面板（日历 + 定时任务，上下布局）。渲染逻辑从 App.tsx 原样搬出，
// 状态/函数由 App 调一次 useCalendar() 传入（notificationCards 跨两处渲染，hook 不能双实例）。
function CalendarPanel({ calendar, onClose }: { calendar: ReturnType<typeof useCalendar>; onClose: () => void }) {
  const {
    calendarViewMonth, eventsByDay,
    handleCalendarMonthChange, handleCalendarDateClick, handlePeriodDrag,
    deleteCalendarEvent, handleCreatePeriodDay,
    showEventForm, selectedDateStr, newEventForm, setNewEventForm, setShowEventForm, createCalendarEvent,
    showNewTaskForm, setShowNewTaskForm,
    tasks, taskTab, setTaskTab, taskPage, checkupPage, setTaskPage, setCheckupPage,
    expandedTasks, setExpandedTasks, toggleTaskActive, deleteTask,
    newTaskForm, setNewTaskForm, createTask,
  } = calendar;

  return (
    <div className="task-panel">
      <div className="task-panel-header">
        <h3>📋 日程管理</h3>
        <button
          className="task-panel-close"
          onClick={onClose}
          title="关闭"
        >
          ×
        </button>
      </div>
      <div className="task-panel-content">
        {/* ── 日历组件 ── */}
        <div className="calendar-section">
          <CalendarGrid
            year={calendarViewMonth.getFullYear()}
            month={calendarViewMonth.getMonth() + 1}
            eventsByDay={eventsByDay}
            onMonthChange={handleCalendarMonthChange}
            onDateClick={handleCalendarDateClick}
            onPeriodDrag={handlePeriodDrag}
            onDeleteEvent={deleteCalendarEvent}
            onCreatePeriodDay={handleCreatePeriodDay}
          />
        </div>

        {/* 日历事件创建表单 */}
        {showEventForm && (
          <div className="event-form-overlay">
            <div className="event-form">
              <h4>{selectedDateStr} — 新建事件</h4>
              <label>
                标题
                <input
                  type="text"
                  value={newEventForm.title}
                  onChange={e => setNewEventForm(prev => ({ ...prev, title: e.target.value }))}
                  placeholder="事件标题"
                />
              </label>
              <label>
                备注
                <textarea
                  value={newEventForm.description}
                  onChange={e => setNewEventForm(prev => ({ ...prev, description: e.target.value }))}
                  placeholder="备注（可选）"
                  rows={2}
                />
              </label>
              <label>
                类型
                <select
                  value={newEventForm.event_type}
                  onChange={e => setNewEventForm(prev => ({ ...prev, event_type: e.target.value as 'memorial' | 'period' }))}
                >
                  <option value="memorial">纪念日</option>
                  <option value="period">生理期（单日）</option>
                </select>
              </label>
              <label>
                创建人
                <select
                  value={newEventForm.creator}
                  onChange={e => setNewEventForm(prev => ({ ...prev, creator: e.target.value as 'AI' | '人类伙伴' }))}
                >
                  <option value="AI">AI</option>
                  <option value="人类伙伴">人类伙伴</option>
                </select>
              </label>
              <div className="event-form-actions">
                <button className="task-form-cancel" onClick={() => setShowEventForm(false)}>取消</button>
                <button className="task-form-submit" onClick={createCalendarEvent} disabled={!newEventForm.title.trim()}>创建</button>
              </div>
            </div>
          </div>
        )}

        {/* ── 任务标签页 ── */}
        {!showNewTaskForm ? (
          <>
            {tasks.length === 0 ? (
              <div className="task-empty">
                <p>暂无定时任务</p>
                <p className="help-text">AI 可以在对话中为你创建定时任务，也可以手动添加</p>
              </div>
            ) : (
              <>
                {/* 类别标签 */}
                <div className="task-tabs">
                  {(['regular', 'checkup'] as const).map(tab => {
                    const count = tasks.filter(t =>
                      tab === 'checkup'
                        ? (t.task_type === 'inconsistency' || t.task_type === 'self_task')
                        : (t.task_type !== 'inconsistency' && t.task_type !== 'self_task')
                    ).length;
                    if (count === 0) return null;
                    return (
                      <button key={tab}
                        className={`task-tab ${taskTab === tab ? 'active' : ''}`}
                        onClick={() => setTaskTab(tab)}
                      >
                        {tab === 'regular' ? '⏰ 日程提醒' : '🤖 查岗·自动任务'}
                        <span className="task-tab-count">{count}</span>
                      </button>
                    );
                  })}
                </div>

                {/* 当前标签页的分页列表 */}
                {(() => {
                  const tabTasks = tasks.filter(t =>
                    taskTab === 'checkup'
                      ? (t.task_type === 'inconsistency' || t.task_type === 'self_task')
                      : (t.task_type !== 'inconsistency' && t.task_type !== 'self_task')
                  );
                  const PAGE = 5;
                  const totalPages = Math.max(1, Math.ceil(tabTasks.length / PAGE));
                  const isCheckupTab = taskTab === 'checkup';
                  const page = isCheckupTab ? checkupPage : taskPage;
                  const setPage = isCheckupTab ? setCheckupPage : setTaskPage;
                  const currentPage = Math.min(page, totalPages - 1);
                  const pageTasks = tabTasks.slice(currentPage * PAGE, (currentPage + 1) * PAGE);
                  const activeCount = tabTasks.filter(t => t.is_active).length;
                  return (
                    <>
                      <div className="task-list">
                        {pageTasks.map(task => (
                          <TaskItemRow key={task.id} task={task}
                            onToggle={toggleTaskActive} onDelete={deleteTask}
                            isCheckup={taskTab === 'checkup'}
                            isExpanded={expandedTasks.has(task.id)}
                            onToggleExpand={() => setExpandedTasks(prev => {
                              const next = new Set(prev);
                              if (next.has(task.id)) next.delete(task.id);
                              else next.add(task.id);
                              return next;
                            })}
                          />
                        ))}
                      </div>
                      <div className="task-pagination">
                        <button disabled={currentPage === 0}
                          onClick={() => setPage(Math.max(0, currentPage - 1))}>←</button>
                        <span>{currentPage + 1}/{totalPages} ({tabTasks.length}个, {activeCount}活跃)</span>
                        <button disabled={currentPage >= totalPages - 1}
                          onClick={() => setPage(Math.min(totalPages - 1, currentPage + 1))}>→</button>
                      </div>
                    </>
                  );
                })()}
              </>
            )}
            <button
              className="task-add-btn"
              onClick={() => setShowNewTaskForm(true)}
            >
              + 新建定时任务
            </button>
          </>
        ) : (
          <div className="task-form">
            <h4>新建定时任务</h4>
            <label>
              标题
              <input
                type="text"
                value={newTaskForm.title}
                onChange={e => setNewTaskForm(prev => ({ ...prev, title: e.target.value }))}
                placeholder="任务标题"
              />
            </label>
            <label>
              描述
              <textarea
                value={newTaskForm.description}
                onChange={e => setNewTaskForm(prev => ({ ...prev, description: e.target.value }))}
                placeholder="任务描述（可选）"
                rows={2}
              />
            </label>
            <label>
              提醒原因
              <textarea
                value={newTaskForm.reason}
                onChange={e => setNewTaskForm(prev => ({ ...prev, reason: e.target.value }))}
                placeholder="为什么要设置这个提醒？如：用户经常熬夜"
                rows={2}
              />
              <span className="task-form-helptext">AI 会根据此原因生成个性化的提醒消息</span>
            </label>
            <label>
              AI 消息上下文
              <textarea
                value={newTaskForm.ai_context}
                onChange={e => setNewTaskForm(prev => ({ ...prev, ai_context: e.target.value }))}
                placeholder="希望 AI 说什么？如：用关心的语气提醒用户该睡觉了"
                rows={2}
              />
              <span className="task-form-helptext">触发时 AI 会以此为上下文生成自然口语消息</span>
            </label>
            <label>
              触发方式
              <select
                value={newTaskForm.trigger_type}
                onChange={e => setNewTaskForm(prev => ({ ...prev, trigger_type: e.target.value as 'cron' | 'datetime' }))}
              >
                <option value="datetime">一次性时间</option>
                <option value="cron">周期任务 (Cron)</option>
              </select>
            </label>
            {newTaskForm.trigger_type === 'datetime' ? (
              <label>
                触发时间
                <input
                  type="datetime-local"
                  value={newTaskForm.trigger_config.datetime || ''}
                  onChange={e => setNewTaskForm(prev => ({ ...prev, trigger_config: { datetime: e.target.value } }))}
                />
              </label>
            ) : (
              <label>
                Cron 表达式
                <input
                  type="text"
                  value={newTaskForm.trigger_config.cron || ''}
                  onChange={e => setNewTaskForm(prev => ({ ...prev, trigger_config: { cron: e.target.value } }))}
                  placeholder="例: 0 9 * * * (每天9点)"
                />
              </label>
            )}
            <label>
              通知方式
              <div className="task-notify-options">
                <label className="task-notify-checkbox">
                  <input
                    type="checkbox"
                    checked={newTaskForm.notify_method.includes('popup')}
                    onChange={e => {
                      const methods = e.target.checked
                        ? [...newTaskForm.notify_method, 'popup']
                        : newTaskForm.notify_method.filter(m => m !== 'popup');
                      setNewTaskForm(prev => ({ ...prev, notify_method: methods }));
                    }}
                  />
                  桌面弹窗
                </label>
                <label className="task-notify-checkbox">
                  <input
                    type="checkbox"
                    checked={newTaskForm.notify_method.includes('message')}
                    onChange={e => {
                      const methods = e.target.checked
                        ? [...newTaskForm.notify_method, 'message']
                        : newTaskForm.notify_method.filter(m => m !== 'message');
                      setNewTaskForm(prev => ({ ...prev, notify_method: methods }));
                    }}
                  />
                  聊天消息
                </label>
                <label className="task-notify-checkbox">
                  <input
                    type="checkbox"
                    checked={newTaskForm.alarm_enabled}
                    onChange={e => setNewTaskForm(prev => ({ ...prev, alarm_enabled: e.target.checked }))}
                  />
                  手机闹钟
                </label>
              </div>
              {newTaskForm.alarm_enabled && (
                <span className="task-form-helptext">到点响铃；按掉后 5 分钟仍未回复，AI 会再提醒一句</span>
              )}
            </label>
            <label>
              消息内容
              <textarea
                value={newTaskForm.message_content}
                onChange={e => setNewTaskForm(prev => ({ ...prev, message_content: e.target.value }))}
                placeholder="触发时发送的消息内容（可选）"
                rows={2}
              />
            </label>
            <div className="task-form-actions">
              <button className="task-form-cancel" onClick={() => setShowNewTaskForm(false)}>
                取消
              </button>
              <button
                className="task-form-submit"
                onClick={createTask}
                disabled={!newTaskForm.title.trim()}
              >
                创建
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default CalendarPanel;
