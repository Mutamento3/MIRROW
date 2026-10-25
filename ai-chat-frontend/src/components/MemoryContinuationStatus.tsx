import { useEffect, useState } from 'react';
import { readMemory } from '../hooks/useMemory';

type Progress = {
  status: string; active_date?: string; remaining_count?: number; committed_page_count?: number;
};

export default function MemoryContinuationStatus() {
  const [revision, setRevision] = useState(0);
  const [progress, setProgress] = useState<Progress | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    readMemory<Progress>('continuation-progress', controller.signal)
      .then(data => { if (!controller.signal.aborted) setProgress(data); })
      .catch(() => { if (!controller.signal.aborted) setProgress({ status: 'unavailable' }); });
    return () => controller.abort();
  }, [revision]);
  const messages: Record<string, string> = {
    pending: '本轮按页处理；未完成部分留待下一次日结或既有补漏检查。',
    complete: '本日有效续接候选已判定；不代表相似经历都会合并成事件链。',
    attention_required: '有候选等待复核，当前不能安全自动继续；已有记忆仍保留。',
    needs_replan: '来源或链尾已变化，需要重新规划候选；当前不猜补关联。',
    unavailable: '续接进度暂不可读取，不代表候选已完成。',
    not_started: '尚无可查看的正式日结。', disabled: '续接处理当前未启用。',
  };
  return <section aria-label="事件续接进度" className="memory-continuation-status">
    <p><strong>最近日结的续接进度{progress?.active_date ? ` · ${progress.active_date}` : ''}</strong>
      {' '}<button type="button" className="btn-secondary" onClick={() => setRevision(value => value + 1)}>查看最新进度</button></p>
    <p role="status">{progress ? messages[progress.status] || messages.unavailable : '正在读取续接进度…'}
      {progress?.remaining_count !== undefined && ` 已提交 ${progress.committed_page_count ?? 0} 页，剩余 ${progress.remaining_count} 个候选关联（不是事件数量）。`}</p>
  </section>;
}
