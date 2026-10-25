import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import type { ToolCallRecord } from '../types';
import './GameEventCard.css';

type GameChapter = {
  playthrough_key: string;
  number: number | string | null;
  title: string;
  protagonist: string;
  choices: { node_id: string; label: string; text: string; reason: string }[];
  ending: string;
  reflection: string;
};

/** The SQL-owned Wander event is the receipt; this card only renders it. */
export default function GameEventCard({ summary, call, eventId }: { summary: string; call: ToolCallRecord; eventId: string }) {
  const game = call.extra_data?.game_summary as {
    game?: string; game_name?: string; slot?: number; completed?: boolean; chapters?: GameChapter[]
  } | undefined;
  const attached = Array.isArray(game?.chapters) ? game.chapters : [];
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<GameChapter[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const activityId = /^activity_event_([a-f0-9]{32})$/i.exec(eventId)?.[1];
  const chapters = attached.length ? attached : loaded;
  const legacyFallback = summary.startsWith('刚才在🎮 玩游戏，完成了：');
  const fallback = legacyFallback || summary.includes('整体感想暂未形成；');
  const lastChapter = chapters[chapters.length - 1];
  const choiceCount = chapters.reduce((total, chapter) => total + (chapter.choices?.length || 0), 0);
  const readableFallback = `${game?.game_name || '这次游戏'}${lastChapter?.number != null ? `推进到第${lastChapter.number}章` : '推进了一段'}${lastChapter?.title ? `《${lastChapter.title}》` : ''}${choiceCount ? `，作出了${choiceCount}次选择` : ''}${game?.completed ? '，本周目已确认完结' : ''}。整体感想暂未形成；实际选择和当时留下的想法可在游戏记录中查看。`;
  const displayedSummary = legacyFallback ? readableFallback : summary || call.result || '';

  useEffect(() => {
    if (!open || attached.length || loaded.length || !activityId || (game?.game && game.game !== 'detroit')) return;
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      try {
        const query = new URLSearchParams({ activity_id: activityId });
        const response = await fetch(`${getApiBase()}/api/wander/games/activity-chapters?${query}`, {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw new Error('章节记录暂时无法读取');
        const data = await response.json() as { chapters?: GameChapter[] };
        if (!controller.signal.aborted) setLoaded(Array.isArray(data.chapters) ? data.chapters : []);
        setError('');
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '章节记录暂时无法读取');
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [open, attached.length, loaded.length, activityId, game?.game]);

  return <details className="game-event-card" onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary><span aria-hidden="true">🎮</span><strong>{game?.game_name || call.description || 'AI 的游戏经历'}</strong><small>{game?.slot ? `槽位 ${game.slot} · ` : ''}{game?.completed ? (game.game === 'mbti' || game.game === 'sins_virtues' ? '测试完成' : '周目完结') : chapters.length ? `${chapters.length} 章经历` : '阶段经历'}</small></summary>
    {chapters.length > 0 && <div className="game-event-chapters">
      {chapters.map((chapter, index) => <details key={`${chapter.playthrough_key}-${chapter.number}-${chapter.title}-${index}`}>
        <summary><strong>{chapter.number != null ? `第 ${chapter.number} 章` : '章节'} · {chapter.title || '未命名'}</strong><small>{chapter.protagonist}{chapter.choices.length ? ` · ${chapter.choices.length} 次选择` : ''}</small></summary>
        {chapter.choices.length > 0 && <ol>{chapter.choices.map((choice, choiceIndex) => <li key={`${choice.node_id}-${choiceIndex}`}>
          <strong>{choice.label || '实际选择'}</strong>{choice.text && <> · {choice.text}</>}
          {choice.reason && <p>当时的缘由：{choice.reason}</p>}
        </li>)}</ol>}
        {chapter.ending && <p>本章结局：{chapter.ending}</p>}
        {chapter.reflection && <p>AI 保存的章末感想：{chapter.reflection}</p>}
        {!chapter.reflection && <p>这章尚未保存章末感想。</p>}
      </details>)}
    </div>}
    {loading && <p role="status">正在翻开本次章节…</p>}
    {error && <p role="status">{error}</p>}
    {displayedSummary ? <details className="game-event-overall"><summary>{fallback ? '本次回看' : '整段感想'}</summary><p>{displayedSummary}</p></details>
      : chapters.length === 0 && !loading && <p>这次活动还没有可展示的总结。</p>}
  </details>;
}
