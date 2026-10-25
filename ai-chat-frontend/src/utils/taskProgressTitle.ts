export function taskProgressTitle(tasks: Record<string, { status?: string; purpose?: string }> | { status?: string; purpose?: string }[]) {
  const all = Object.values(tasks);
  const live = all.filter(task => task.status === 'pending' || task.status === 'running');
  const current = live.length ? live : all;
  if (current.length && current.every(task => task.purpose === 'memory_continuation')) {
    return '正在关联历史经历…';
  }
  if (current.length && current.every(task => ['memory_recovery', 'memory_continuation'].includes(task.purpose || ''))
      && current.some(task => task.purpose === 'memory_continuation')) {
    return '正在维护往日记忆…';
  }
  return current.length && current.every(task => task.purpose === 'memory_recovery')
    ? '正在补齐往日记忆…' : '正在整理今天的记忆…';
}
