import { useCallback, useEffect, useMemo, useState } from 'react';
import { getApiBase } from '../config';

type SceneMessage = {
  id?: number | string;
  message_id?: string;
  timestamp?: string;
  role?: string;
  content?: string;
  is_wander?: boolean | number | string;
  is_sentinel?: boolean | number | string;
  is_reminder?: boolean | number | string;
  tool_summary?: string;
};

type Scene = {
  id: string;
  active_date?: string;
  session_id?: string;
  start_ts?: string;
  end_ts?: string;
  first_user?: string;
  msg_count?: number;
  user_msg_count?: number;
  recollection_count?: number;
  anchor_types?: string[];
  is_anchor?: boolean;
  messages?: SceneMessage[];
};

type SceneListResponse = {
  date?: string;
  gap_minutes?: number;
  scenes?: Scene[];
};

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const ANCHOR_LABELS: Record<string, string> = {
  memory: '💭 记忆',
  bookmark: '🔖 收藏',
  collection: '🔖 收藏',
  ledger: '📒 账本',
  recollection: '⚑ 回忆锚点',
  emotion_peak: '💗 情绪峰值',
  long_scene: '📏 长 scene',
  important_event: '⭐ 重要事件',
  wander_recall: '🌀 漫想回溯',
};

function localDateString(value = new Date()): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function dateLabel(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : `${value} ${WEEKDAYS[date.getDay()]}`;
}

function moveDate(value: string, offset: number): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  date.setDate(date.getDate() + offset);
  return localDateString(date);
}

function formatTime(value?: string): string {
  if (!value) return '--:--';
  const isoTime = String(value).match(/T(\d{2}:\d{2})/);
  if (isoTime) return isoTime[1];
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 5) || '--:--';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function formatTimeRange(start?: string, end?: string): string {
  const first = formatTime(start);
  const last = formatTime(end);
  return first === last ? first : `${first}–${last}`;
}

function truthyFlag(value: SceneMessage[keyof SceneMessage]): boolean {
  if (typeof value === 'string') {
    return !['', '0', 'false', 'none', 'null'].includes(value.trim().toLowerCase());
  }
  return Boolean(value);
}

function anchorTypes(scene: Scene): string[] {
  return Array.isArray(scene.anchor_types) ? scene.anchor_types.filter(Boolean) : [];
}

function isMarked(scene: Scene): boolean {
  return Number(scene.recollection_count || 0) > 0;
}

function sceneIsAnchor(scene: Scene): boolean {
  return Boolean(scene.is_anchor) || isMarked(scene) || anchorTypes(scene).length > 0;
}

function anchorLabel(type: string): string {
  return ANCHOR_LABELS[type] || type;
}

function messageRole(message: SceneMessage): 'user' | 'assistant' {
  return message.role === 'user' ? 'user' : 'assistant';
}

function messageRoleLabel(message: SceneMessage): string {
  if (message.role === 'user') return '人类伙伴';
  if (message.role === 'assistant') return 'AI';
  return message.role || '消息';
}

type TimelineSceneRowProps = {
  scene: Scene;
  expanded: boolean;
  detail?: Scene;
  detailLoading: boolean;
  markerPending: boolean;
  onToggle: () => void;
  onToggleMarker: () => void;
};

function TimelineSceneRow({
  scene,
  expanded,
  detail,
  detailLoading,
  markerPending,
  onToggle,
  onToggleMarker,
}: TimelineSceneRowProps) {
  const anchor = sceneIsAnchor(scene);
  const marked = isMarked(scene);
  const types = anchorTypes(scene);
  const messages = detail?.messages || [];
  const firstUser = scene.first_user?.trim() || '这段对话暂无首条用户消息摘要';
  const messageCount = detail?.msg_count ?? scene.msg_count ?? messages.length;
  const userMessageCount = detail?.user_msg_count ?? scene.user_msg_count;

  return (
    <div className={`timeline-scene ${anchor ? 'is-anchor' : 'is-iceberg'} ${expanded ? 'is-expanded' : ''}`}>
      <div className="timeline-scene-row">
        <button
          type="button"
          className="timeline-scene-summary"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          <span className="timeline-dot" aria-hidden="true">{anchor ? '●' : '○'}</span>
          <span className="timeline-scene-summary-body">
            <span className="timeline-scene-heading">
              <span className="timeline-scene-time">{formatTimeRange(scene.start_ts, scene.end_ts)}</span>
              <span className="timeline-scene-count">
                {messageCount} 条消息{userMessageCount !== undefined ? ` · ${userMessageCount} 条用户` : ''}
              </span>
            </span>
            <span className="timeline-scene-preview" title={firstUser}>{firstUser}</span>
            <span className="timeline-scene-badges">
              {types.map(type => <span className="timeline-anchor-badge" key={type}>{anchorLabel(type)}</span>)}
              {marked && <span className="timeline-recollection-badge">⚑ 回忆锚点 ×{scene.recollection_count}</span>}
              {!anchor && <span className="timeline-iceberg-badge">冰山 · 尘封</span>}
            </span>
          </span>
          <span className="timeline-expand-hint" aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
        </button>
        <button
          type="button"
          className={`timeline-flag-button ${marked ? 'active' : ''}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggleMarker();
          }}
          disabled={markerPending}
          aria-label={marked ? '取消回忆锚点' : '标记为回忆锚点'}
          aria-pressed={marked}
          title={marked ? '取消回忆锚点' : '安放回忆锚点'}
        >
          {marked ? '⚑' : '⚐'}
        </button>
      </div>

      <div className={`timeline-scene-expand ${expanded ? 'open' : ''}`}>
        <div className="timeline-scene-detail">
          {detailLoading && <div className="timeline-detail-status">正在读取这段对话…</div>}
          {!detailLoading && !detail && expanded && (
            <div className="timeline-detail-status">原文暂时无法读取，收起后再次展开可重试。</div>
          )}
          {!detailLoading && detail && (
            <>
              <div className="timeline-detail-meta">
                <span>🕒 {formatTimeRange(detail.start_ts || scene.start_ts, detail.end_ts || scene.end_ts)}</span>
                {detail.session_id && <span>会话 {detail.session_id}</span>}
                <span>{detail.msg_count ?? messages.length} 条消息</span>
                {detail.user_msg_count !== undefined && <span>{detail.user_msg_count} 条用户消息</span>}
                {anchorTypes(detail).map(type => <span className="timeline-anchor-badge" key={`detail-${type}`}>{anchorLabel(type)}</span>)}
              </div>
              <div className="timeline-messages">
                {messages.length === 0 ? (
                  <div className="timeline-detail-status">这段 scene 没有可显示的原文。</div>
                ) : messages.map((message, index) => {
                  const role = messageRole(message);
                  const flags = [
                    truthyFlag(message.is_wander) ? '💭 漫想' : '',
                    truthyFlag(message.is_sentinel) ? '🛡️ 哨兵' : '',
                    truthyFlag(message.is_reminder) ? '⏰ 提醒' : '',
                  ].filter(Boolean);
                  const key = message.message_id || message.id || `${message.timestamp || 'message'}-${index}`;
                  return (
                    <div className={`timeline-message ${role}`} key={String(key)}>
                      <div className="timeline-message-meta">
                        <span>{messageRoleLabel(message)}</span>
                        {flags.map(flag => <span className="timeline-message-flag" key={flag}>{flag}</span>)}
                        {message.tool_summary && <span className="timeline-message-tool">🔧 {message.tool_summary}</span>}
                        <span className="timeline-message-time">{formatTime(message.timestamp)}</span>
                      </div>
                      <div className="timeline-message-content">{message.content || '（空消息）'}</div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function TimelineView() {
  const [selectedDate, setSelectedDate] = useState(localDateString);
  const [gapMinutes, setGapMinutes] = useState(15);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, Scene>>({});
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({});
  const [markerPending, setMarkerPending] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadScenes = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams({ date: selectedDate, gap_minutes: String(gapMinutes) });
      const response = await fetch(`${getApiBase()}/api/scenes?${query.toString()}`, { signal });
      if (!response.ok) throw new Error(`加载时间轴失败（${response.status}）`);
      const data = await response.json() as SceneListResponse;
      setScenes(Array.isArray(data.scenes) ? data.scenes : []);
      setDetails({});
      setExpandedId(null);
    } catch (loadError) {
      if (loadError instanceof DOMException && loadError.name === 'AbortError') return;
      if (loadError instanceof Error && loadError.name === 'AbortError') return;
      setError(loadError instanceof Error ? loadError.message : '加载时间轴失败');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [gapMinutes, selectedDate]);

  useEffect(() => {
    const controller = new AbortController();
    void loadScenes(controller.signal);
    return () => controller.abort();
  }, [loadScenes]);

  const orderedScenes = useMemo(
    () => [...scenes].sort((a, b) => String(a.start_ts || '').localeCompare(String(b.start_ts || ''))),
    [scenes],
  );

  const loadSceneDetail = useCallback(async (scene: Scene) => {
    if (details[scene.id] || detailLoading[scene.id]) return;
    setDetailLoading(previous => ({ ...previous, [scene.id]: true }));
    try {
      const response = await fetch(`${getApiBase()}/api/scenes/${encodeURIComponent(scene.id)}`);
      if (!response.ok) throw new Error(`原文读取失败（${response.status}）`);
      const data = await response.json() as Scene;
      setDetails(previous => ({ ...previous, [scene.id]: data }));
    } catch (loadError) {
      console.error('读取 scene 原文失败:', loadError);
    } finally {
      setDetailLoading(previous => ({ ...previous, [scene.id]: false }));
    }
  }, [detailLoading, details]);

  const toggleScene = (scene: Scene) => {
    if (expandedId === scene.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(scene.id);
    void loadSceneDetail(scene);
  };

  const toggleRecollection = async (scene: Scene) => {
    if (markerPending[scene.id]) return;
    const marked = isMarked(scene);
    const action = marked ? 'remove' : 'add';
    const previousScenes = scenes;
    const previousDetail = details[scene.id];
    const currentCount = Number(scene.recollection_count || 0);
    const nextCount = Math.max(0, currentCount + (marked ? -1 : 1));
    const currentTypes = anchorTypes(scene);
    const nextTypes = marked
      ? currentTypes.filter(type => type !== 'recollection')
      : Array.from(new Set([...currentTypes, 'recollection']));

    setMarkerPending(previous => ({ ...previous, [scene.id]: true }));
    setScenes(previous => previous.map(item => item.id === scene.id ? {
      ...item,
      recollection_count: nextCount,
      anchor_types: nextTypes,
      is_anchor: nextCount > 0 || nextTypes.length > 0,
    } : item));
    if (previousDetail) {
      setDetails(previous => ({
        ...previous,
        [scene.id]: {
          ...previousDetail,
          recollection_count: nextCount,
          anchor_types: nextTypes,
          is_anchor: nextCount > 0 || nextTypes.length > 0,
        },
      }));
    }

    try {
      const response = await fetch(`${getApiBase()}/api/scenes/${encodeURIComponent(scene.id)}/recollection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!response.ok) throw new Error(`回忆锚点更新失败（${response.status}）`);
      const data = await response.json() as { scene?: Scene };
      const updated = data.scene;
      if (updated) {
        setScenes(previous => previous.map(item => item.id === scene.id ? { ...item, ...updated } : item));
        setDetails(previous => previous[scene.id] ? { ...previous, [scene.id]: { ...previous[scene.id], ...updated } } : previous);
      }
      setError('');
    } catch (updateError) {
      setScenes(previousScenes);
      if (previousDetail) {
        setDetails(previous => ({ ...previous, [scene.id]: previousDetail }));
      } else {
        setDetails(previous => {
          const next = { ...previous };
          delete next[scene.id];
          return next;
        });
      }
      setError(updateError instanceof Error ? updateError.message : '回忆锚点更新失败，已恢复');
    } finally {
      setMarkerPending(previous => ({ ...previous, [scene.id]: false }));
    }
  };

  return (
    <section className="timeline-view" aria-label="时间轴">
      <div className="timeline-toolbar">
        <div className="timeline-date-controls">
          <button type="button" className="timeline-date-arrow" onClick={() => setSelectedDate(value => moveDate(value, -1))} aria-label="前一天">‹</button>
          <input
            type="date"
            value={selectedDate}
            onChange={event => setSelectedDate(event.target.value)}
            aria-label="选择日期"
          />
          <button type="button" className="timeline-date-arrow" onClick={() => setSelectedDate(value => moveDate(value, 1))} aria-label="后一天">›</button>
        </div>
        <label className="timeline-gap-control">
          <span>断档阈值</span>
          <input
            type="range"
            min="5"
            max="60"
            step="1"
            value={gapMinutes}
            onChange={event => setGapMinutes(Number(event.target.value))}
            aria-label="断档阈值（分钟）"
          />
          <output>{gapMinutes} 分钟</output>
        </label>
        <p className="timeline-preview-note">仅预览本次分段，持久索引仍按配置阈值。</p>
      </div>

      <div className="timeline-day-header">
        <span>──</span>
        <strong>{dateLabel(selectedDate)}</strong>
        <span>──</span>
      </div>

      {error && (
        <div className="timeline-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadScenes()}>重试</button>
        </div>
      )}
      {loading ? (
        <div className="timeline-status">正在读取这一天的 scene…</div>
      ) : orderedScenes.length === 0 ? (
        <div className="timeline-empty">
          <div className="memory-empty-icon">🕰️</div>
          <p>这一天还没有对话 scene</p>
          <p>可以选择其他日期，或调整断档阈值预览。</p>
        </div>
      ) : (
        <div className="timeline-track">
          {orderedScenes.map(scene => (
            <TimelineSceneRow
              key={scene.id}
              scene={scene}
              expanded={expandedId === scene.id}
              detail={details[scene.id]}
              detailLoading={Boolean(detailLoading[scene.id])}
              markerPending={Boolean(markerPending[scene.id])}
              onToggle={() => toggleScene(scene)}
              onToggleMarker={() => void toggleRecollection(scene)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default TimelineView;
