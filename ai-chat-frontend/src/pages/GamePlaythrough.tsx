import { useEffect, useState } from 'react';
import { getApiBase } from '../config';

export type Playthrough = { playthrough_key: string; game: string; slot: number; steps: number; completed: boolean; last_played_at: string; summary: string; last_action: string; checkpoint?: Record<string, unknown> };
type Step = { node_id: string; index: number; at: string; status: 'completed' | 'unconfirmed'; action: string; choice: string; reason: string; scene: string; options: { label: string; text: string }[]; outcome: string; result_summary: string; reflection: string };
type Chronicle = { playthrough_key: string; game: string; slot: number; chapters: { key: string; title: string; steps: Step[] }[] };

export default function GamePlaythrough({ item }: { item: Playthrough }) {
  const [open, setOpen] = useState(false);
  const [chronicle, setChronicle] = useState<Chronicle | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const params = new URLSearchParams({ key: item.playthrough_key });
        const response = await fetch(`${getApiBase()}/api/wander/games/playthrough?${params}`, {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw new Error('章节记录暂时无法读取');
        setChronicle(await response.json() as Chronicle);
        setError('');
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '章节记录暂时无法读取');
      }
    };
    void load();
    return () => controller.abort();
  }, [open, item.playthrough_key, item.steps]);

  return <details className="machine-life-playthrough" onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>槽位 {item.slot} · {item.game === 'fishing' ? '持续收集' : item.completed ? '已完成' : '进行中'} · {item.steps} 步 <small>{item.last_played_at.slice(0, 16).replace('T', ' ')}</small></summary>
    {item.summary && <p className="machine-life-playthrough-summary">本段心得：{item.summary}</p>}
    {error && <p role="status">{error}</p>}
    {open && !chronicle && !error && <p>正在翻开游玩记录…</p>}
    {chronicle?.chapters.map((chapter, chapterIndex) => <details className="machine-life-chapter" key={`${chapter.key}-${chapterIndex}`}>
      <summary>{chapter.title} <small>{chapter.steps.length} 步</small></summary>
      {chapter.steps.map(step => <details className="machine-life-step" key={step.node_id}>
        <summary>第 {step.index} 步 · {step.status === 'completed' ? (step.choice || step.action || '继续游玩') : '结果未确认'} <small>{step.at.slice(11, 16)}</small></summary>
        {step.status !== 'completed' ? <p>这一步执行失败或中断，没有已确认的游戏结果；不会把未完成的选择写成剧情。</p> : <>
          {step.scene && <section><strong>行动前的画面</strong><p>{step.scene}</p></section>}
          {step.options.length > 0 && <section><strong>当时可见的全部选项</strong><ol>{step.options.map((option, index) => <li key={`${index}-${option.label}`} className={option.label === step.choice ? 'selected' : ''}><span>{option.label}</span>{option.text && <span> · {option.text}</span>}{option.label === step.choice && <em> ← AI 选择</em>}</li>)}</ol></section>}
          <section><strong>AI 的选择</strong><p>{step.choice || '当时未单独记录选择文字'}</p></section>
          <section><strong>选择原因</strong><p>{step.reason || '当时未记录原因'}</p></section>
          <section><strong>节点结局／后续画面</strong><p>{step.outcome || step.result_summary || '没有可展示的返回画面'}</p></section>
          {step.reflection && <section><strong>AI 的节点感想</strong><p>{step.reflection}</p></section>}
        </>}
      </details>)}
    </details>)}
  </details>;
}
