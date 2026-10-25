import { useCallback, useEffect, useMemo, useState } from 'react';
import { getApiBase } from '../config';
import OpenLoopLifecycle from './OpenLoopLifecycle';

type AffectOwner = 'k_self' | 'human_resonance';

type SourceRef = {
  source_type?: string;
  source_id?: string;
  source_revision?: number;
  relation?: string;
};

export type AffectComponent = {
  component_id: string;
  owner: AffectOwner | string;
  family?: string;
  label?: string;
  reason?: string;
  initial_intensity?: number;
  effective_intensity?: number;
  is_foreground?: boolean;
  persistence_class?: string;
  half_life_seconds?: number;
  started_at?: string;
  last_reinforced_at?: string;
  updated_at?: string;
  resolved_at?: string;
  decay_fraction?: number;
  status?: string;
  branch_state?: string;
  authorship?: string;
  source_refs?: SourceRef[];
  episode_started_at?: string;
  continuity_text?: string;
  active_day_continuity?: 'earlier_active_day' | 'same_active_day' | 'unknown';
};

type AffectState = {
  components: AffectComponent[];
  dominant?: AffectComponent | null;
};

type Projection = {
  version?: number;
  generated_at?: string;
  renderer_self?: string;
  renderer_resonance?: string;
  renderer_resonance_display?: string;
  self?: AffectState;
  resonance?: AffectState;
};

type RecognitionStatus = {
  enabled?: boolean;
  state?: 'paused' | 'waiting' | 'analyzing' | 'ready' | 'failed' | 'idle' | string;
  quiet_minutes?: number;
  active_checkpoint_minutes?: number;
  active_checkpoint_user_messages?: number;
  quiet_due_at?: string;
  last_outcome?: string;
  last_outcome_at?: string;
  last_success_at?: string;
  last_error?: string;
};

type Preset = {
  preset_id: string;
  label: string;
  emoji: string;
  family: string;
  default_intensity: number;
  default_persistence: string;
  dimensions?: Record<string, number | string>;
  reason?: string;
  builtin?: boolean | number;
  enabled?: boolean;
  sort_order?: number;
};

type Checkin = {
  checkin_id: string;
  label: string;
  emoji: string;
  family: string;
  intensity: number;
  persistence: string;
  note: string;
  status: string;
  revision: number;
  created_at?: string;
};

type Overview = {
  settings?: Record<string, any>;
  projection?: Projection;
  presets?: Preset[];
  checkins?: Checkin[];
  recognition?: RecognitionStatus;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

async function fetchAffect(path: string, init: RequestInit = {}, timeoutMs = 12000): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${getApiBase()}${path}`, { ...init, signal: controller.signal });
  } catch (error: any) {
    if (error?.name === 'AbortError') throw new Error('读取心情超时了，请检查连接后重试。');
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

type BoardPage = 'self' | 'resonance' | 'open_loops';
type IntensityChoice = 'light' | 'medium' | 'clear';

const SOURCE_LABELS: Record<string, string> = {
  conversation_message: '最近聊天里的表达',
  human_manual_checkin: '人类伙伴主动记录',
  manual_affect_correction: '人类伙伴这次修正',
  wander_settlement: '漫想收束后的感受',
  legacy_import: '旧记录的连续性',
};

const INTENSITY_VALUES: Record<IntensityChoice, number> = {
  light: 0.25,
  medium: 0.55,
  clear: 0.82,
};

const INTENSITY_LABELS: Record<IntensityChoice, string> = {
  light: '轻轻的 · 25%',
  medium: '中等 · 55%',
  clear: '很明显 · 82%',
};

const PERSISTENCE_LABELS: Record<string, string> = {
  momentary: '短暂 · 半衰期 20 分钟',
  situational: '一阵 · 半衰期 3 小时',
  lingering: '余韵较久 · 半衰期 18 小时',
};

const FAMILY_EMOJI_LEGEND = [
  ['☀️', '愉悦'], ['🌹', '亲近'], ['🌿', '安定'], ['🔹', '兴趣'], ['🌧️', '低落'],
  ['🫧', '担忧'], ['🔥', '恼怒'], ['🫒', '排斥'], ['✨', '惊讶'], ['🌗', '复杂／未归类'],
  ['🌤️', '暂时没有主心情'],
] as const;

/**
 * The original eight IDs remain stable while the built-in, no-reason
 * vocabulary covers all ten Affect families.  These rows are deliberately
 * not editable/deletable from the resonance manager; custom rows are only
 * user-authored fixed-reason presets.
 */
const BUILTIN_MOOD_OPTIONS: Preset[] = [
  { preset_id: 'happy', label: '开心', emoji: '😊', family: '愉悦', default_intensity: .55, default_persistence: 'momentary', dimensions: { valence: .75, arousal: .55, agency: .35 }, builtin: true, enabled: true, sort_order: 10 },
  { preset_id: 'joy_glad', label: '喜悦', emoji: '🌞', family: '愉悦', default_intensity: .55, default_persistence: 'momentary', dimensions: { valence: .80, arousal: .60, agency: .30 }, builtin: true, enabled: true, sort_order: 11 },
  { preset_id: 'joy_content', label: '满足', emoji: '🍃', family: '愉悦', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: .65, arousal: .35, agency: .40 }, builtin: true, enabled: true, sort_order: 12 },
  { preset_id: 'affection_tender', label: '温柔', emoji: '🌹', family: '亲近', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: .70, arousal: .35, agency: .25 }, builtin: true, enabled: true, sort_order: 15 },
  { preset_id: 'affection_attached', label: '牵挂', emoji: '🫶', family: '亲近', default_intensity: .55, default_persistence: 'lingering', dimensions: { valence: .45, arousal: .30, agency: .20 }, builtin: true, enabled: true, sort_order: 16 },
  { preset_id: 'affection_close', label: '亲昵', emoji: '💞', family: '亲近', default_intensity: .60, default_persistence: 'momentary', dimensions: { valence: .75, arousal: .55, agency: .25 }, builtin: true, enabled: true, sort_order: 17 },
  { preset_id: 'safe', label: '安心', emoji: '🌿', family: '安定', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: .55, arousal: .25, agency: .45 }, builtin: true, enabled: true, sort_order: 20 },
  { preset_id: 'calm_peaceful', label: '平静', emoji: '🪷', family: '安定', default_intensity: .45, default_persistence: 'situational', dimensions: { valence: .35, arousal: .15, agency: .35 }, builtin: true, enabled: true, sort_order: 21 },
  { preset_id: 'calm_relaxed', label: '放松', emoji: '🌱', family: '安定', default_intensity: .45, default_persistence: 'situational', dimensions: { valence: .45, arousal: .10, agency: .35 }, builtin: true, enabled: true, sort_order: 22 },
  { preset_id: 'anticipating', label: '期待', emoji: '✨', family: '兴趣', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: .65, arousal: .60, agency: .35 }, builtin: true, enabled: true, sort_order: 30 },
  { preset_id: 'interest_curious', label: '好奇', emoji: '🔎', family: '兴趣', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: .50, arousal: .60, agency: .50 }, builtin: true, enabled: true, sort_order: 31 },
  { preset_id: 'interest_focused', label: '专注', emoji: '🧭', family: '兴趣', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: .45, arousal: .50, agency: .65 }, builtin: true, enabled: true, sort_order: 32 },
  { preset_id: 'tired', label: '疲惫', emoji: '🌙', family: '低落', default_intensity: .60, default_persistence: 'situational', dimensions: { valence: -.25, arousal: .20, agency: -.15 }, builtin: true, enabled: true, sort_order: 40 },
  { preset_id: 'sad', label: '难过', emoji: '💧', family: '低落', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: -.65, arousal: .30, agency: -.30 }, builtin: true, enabled: true, sort_order: 41 },
  { preset_id: 'low_lost', label: '失落', emoji: '🍂', family: '低落', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: -.55, arousal: .35, agency: -.25 }, builtin: true, enabled: true, sort_order: 42 },
  { preset_id: 'worried', label: '担心', emoji: '🫧', family: '担忧', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: -.40, arousal: .55, agency: -.35 }, builtin: true, enabled: true, sort_order: 50 },
  { preset_id: 'worry_nervous', label: '紧张', emoji: '🫨', family: '担忧', default_intensity: .55, default_persistence: 'momentary', dimensions: { valence: -.35, arousal: .70, agency: -.20 }, builtin: true, enabled: true, sort_order: 51 },
  { preset_id: 'worry_uneasy', label: '不安', emoji: '🌫️', family: '担忧', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: -.45, arousal: .50, agency: -.35 }, builtin: true, enabled: true, sort_order: 52 },
  { preset_id: 'irritated', label: '烦躁', emoji: '🌧️', family: '恼怒', default_intensity: .55, default_persistence: 'momentary', dimensions: { valence: -.45, arousal: .65, agency: .05 }, builtin: true, enabled: true, sort_order: 60 },
  { preset_id: 'anger_angry', label: '生气', emoji: '🔥', family: '恼怒', default_intensity: .60, default_persistence: 'momentary', dimensions: { valence: -.55, arousal: .75, agency: .15 }, builtin: true, enabled: true, sort_order: 61 },
  { preset_id: 'anger_frustrated', label: '憋闷', emoji: '💢', family: '恼怒', default_intensity: .55, default_persistence: 'situational', dimensions: { valence: -.45, arousal: .55, agency: -.05 }, builtin: true, enabled: true, sort_order: 62 },
  { preset_id: 'aversion_dislike', label: '反感', emoji: '🫒', family: '排斥', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: -.55, arousal: .45, agency: .20 }, builtin: true, enabled: true, sort_order: 70 },
  { preset_id: 'aversion_disgust', label: '厌恶', emoji: '🫥', family: '排斥', default_intensity: .55, default_persistence: 'momentary', dimensions: { valence: -.70, arousal: .55, agency: .25 }, builtin: true, enabled: true, sort_order: 71 },
  { preset_id: 'aversion_resist', label: '抗拒', emoji: '🛑', family: '排斥', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: -.50, arousal: .50, agency: .45 }, builtin: true, enabled: true, sort_order: 72 },
  { preset_id: 'surprise_amazed', label: '惊讶', emoji: '😮', family: '惊讶', default_intensity: .50, default_persistence: 'momentary', dimensions: { valence: .10, arousal: .75, agency: .05 }, builtin: true, enabled: true, sort_order: 80 },
  { preset_id: 'surprise_unexpected', label: '意外', emoji: '🌠', family: '惊讶', default_intensity: .45, default_persistence: 'momentary', dimensions: { valence: .05, arousal: .65, agency: 0 }, builtin: true, enabled: true, sort_order: 81 },
  { preset_id: 'surprise_delighted', label: '惊喜', emoji: '🎁', family: '惊讶', default_intensity: .60, default_persistence: 'momentary', dimensions: { valence: .70, arousal: .75, agency: .20 }, builtin: true, enabled: true, sort_order: 82 },
  { preset_id: 'numb', label: '麻木', emoji: '🪶', family: '复杂/未归类', default_intensity: .45, default_persistence: 'lingering', dimensions: { valence: -.10, arousal: .10, agency: -.20 }, builtin: true, enabled: true, sort_order: 90 },
  { preset_id: 'complex_conflicted', label: '矛盾', emoji: '🪢', family: '复杂/未归类', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: 0, arousal: .45, agency: 0 }, builtin: true, enabled: true, sort_order: 91 },
  { preset_id: 'complex_mixed', label: '混杂', emoji: '🌀', family: '复杂/未归类', default_intensity: .50, default_persistence: 'situational', dimensions: { valence: 0, arousal: .40, agency: 0 }, builtin: true, enabled: true, sort_order: 92 },
];

const BUILTIN_PRESET_IDS = new Set(BUILTIN_MOOD_OPTIONS.map(item => item.preset_id));

function presetReason(preset: Preset): string {
  const direct = String(preset.reason || '').trim();
  if (direct) return direct;
  const nested = preset.dimensions?.reason;
  return typeof nested === 'string' ? nested.trim() : '';
}

function isBuiltinPreset(preset: Preset): boolean {
  // Once the backend supplies the authority bit, trust it.  The ID fallback
  // exists only for an older backend that did not expose ``builtin`` yet.
  if (preset.builtin !== undefined) return preset.builtin === true || preset.builtin === 1;
  return BUILTIN_PRESET_IDS.has(preset.preset_id);
}

function builtinOptionsFromPresets(presets: Preset[]): Preset[] {
  const byId = new Map(presets.filter(isBuiltinPreset).map(item => [item.preset_id, item]));
  const clean = (item: Preset): Preset => {
    const dimensions = { ...(item.dimensions || {}) };
    delete dimensions.reason;
    return { ...item, dimensions, builtin: true, enabled: true };
  };
  const known = BUILTIN_MOOD_OPTIONS.map(base => clean({ ...base, ...(byId.get(base.preset_id) || {}) }));
  const extra = presets.filter(item => isBuiltinPreset(item) && !BUILTIN_PRESET_IDS.has(item.preset_id)).map(clean);
  return [...known, ...extra];
}

function makeClientActionKey(kind: string, componentId: string): string {
  const nonce = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `affect-${kind}:${componentId}:${nonce}`;
}

/** Map model/user family wording to a small, stable visual vocabulary. */
export function affectFamilyTone(family?: string, label?: string): string {
  const text = `${family || ''} ${label || ''}`.toLocaleLowerCase().replace(/[\s/|·_-]+/g, '');
  if (/worry|anx|fear|担忧|焦虑|紧张|害怕|恐惧|不安|忐忑/.test(text)) return 'worry';
  if (/anger|angry|irritat|愤怒|恼怒|烦躁|生气|发火|恼火/.test(text)) return 'anger';
  if (/aversion|disgust|厌恶|反感|嫌弃|排斥|恶心/.test(text)) return 'aversion';
  if (/surprise|惊讶|惊奇|意外|惊喜/.test(text)) return 'surprise';
  if (/affection|love|tender|亲近|亲密|依恋|爱意|心疼|温柔|牵挂/.test(text)) return 'affection';
  if (/interest|curious|anticipat|兴趣|好奇|期待|探索/.test(text)) return 'interest';
  if (/sadness|sad|grief|低落|难过|悲伤|痛苦|失落|疲惫|委屈|负面/.test(text)) return 'sadness';
  if (/calm|peace|serene|平静|安定|安心|松弛|放松|宁静/.test(text)) return 'calm';
  if (/joy|happy|glad|开心|喜悦|快乐|愉悦|幸福|正面/.test(text)) return 'joy';
  return 'complex';
}

export function affectEmoji(family?: string, label?: string): string {
  const tone = affectFamilyTone(family, label);
  return {
    joy: '☀️', affection: '🌹', calm: '🌿', interest: '🔹', sadness: '🌧️',
    worry: '🫧', anger: '🔥', aversion: '🫒', surprise: '✨', complex: '🌗',
  }[tone] || '🌤️';
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function AffectEmojiHelp({ onClose }: { onClose: () => void }) {
  const groupedPresets = BUILTIN_MOOD_OPTIONS.reduce<Record<string, Preset[]>>((result, preset) => {
    (result[preset.family] ||= []).push(preset);
    return result;
  }, {});
  return (
    <aside className="affect-emoji-help" aria-label="心情图标说明">
      <header><div><b>这些图标是什么意思？</b><small>顶栏显示大类，预设卡片显示更具体的心情。</small></div><button type="button" onClick={onClose} aria-label="收起图标说明">×</button></header>
      <div className="affect-emoji-family-grid">{FAMILY_EMOJI_LEGEND.map(([emoji, label]) => <span key={label}><i aria-hidden="true">{emoji}</i>{label}</span>)}</div>
      <p className="affect-emoji-note">例如：顶栏的 🌿 是“安定类”；预设里的 🌿 则特指“安心”——不是在说草。</p>
      <details className="affect-emoji-preset-details">
        <summary>查看预设里的全部细分图标</summary>
        <div>{Object.entries(groupedPresets).map(([family, items]) => <section key={family}><b>{family}</b><p>{items.map(item => <span key={item.preset_id}><i aria-hidden="true">{item.emoji}</i>{item.label}</span>)}</p></section>)}</div>
      </details>
    </aside>
  );
}

function formatDate(value?: string): string {
  if (!value) return '还没有时间记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {
    year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function continuityInfo(value?: string, _continuity?: AffectComponent['active_day_continuity'], text?: string): string {
  if (text) return text;
  if (!value) return '持续时间未知';
  const started = new Date(value);
  if (Number.isNaN(started.getTime())) return '持续时间未知';
  const now = new Date();
  const hours = Math.max(0, (now.getTime() - started.getTime()) / 3600000);
  const duration = hours < 1 ? `约 ${Math.max(1, Math.round(hours * 60))} 分钟` : `约 ${Math.round(hours)} 小时`;
  return `已持续${duration}`;
}

function naturalText(value?: string, fallback = '目前没有更多可读理由。'): string {
  const text = String(value || '').trim();
  if (!text) return fallback;
  return text
    .replace(/\b(?:component|source|message|msg|assistant|user|episode|transition|checkin|wander)_[A-Za-z0-9:-]+\b/gi, '相关证据')
    .replace(/\b(?:source_id|component_id|message_id|episode_id|transition_id)\s*[:=]\s*[^，。；;\s]+/gi, '相关证据');
}

function rendererPreview(renderer?: string): string {
  const lines = String(renderer || '').split('\n').map(line => line.trim()).filter(Boolean);
  return lines.filter(line => !/^\[[^\]]+\]$/.test(line)).join(' ') || 'Affect 会在真实材料收束后自然更新。';
}

function sourceLabels(component: AffectComponent): string[] {
  const labels = (component.source_refs || []).map(ref => SOURCE_LABELS[String(ref.source_type || '')] || '有来源的记录');
  return [...new Set(labels)];
}

function decayInfo(component: AffectComponent): { text: string; width: number } {
  const raw = Number(component.decay_fraction);
  const fraction = Number.isFinite(raw)
    ? clamp(raw, 0, 1)
    : clamp(1 - Number(component.effective_intensity || 0) / Math.max(0.01, Number(component.initial_intensity || 1)), 0, 1);
  if (fraction < 0.2) return { text: '还很鲜明，正在自然展开', width: Math.round((1 - fraction) * 100) };
  if (fraction < 0.55) return { text: '正在慢慢退潮，余韵还在', width: Math.round((1 - fraction) * 100) };
  return { text: '余韵较轻，接近自然淡去', width: Math.round((1 - fraction) * 100) };
}

function intensityChoice(value?: number): IntensityChoice {
  const amount = Number(value || 0);
  return amount >= 0.68 ? 'clear' : amount >= 0.38 ? 'medium' : 'light';
}

function StickyCard({
  component,
  primary = false,
  onClick,
}: {
  component: AffectComponent;
  primary?: boolean;
  onClick: () => void;
}) {
  const tone = affectFamilyTone(component.family, component.label);
  const decay = decayInfo(component);
  return (
    <button
      type="button"
      className={`affect-sticky affect-sticky-${primary ? 'primary' : 'secondary'}`}
      data-family={tone}
      onClick={onClick}
      aria-label={`查看${component.label || '这份情绪'}详情`}
    >
      <span className="affect-sticky-tape" aria-hidden="true" />
      <span className="affect-sticky-emoji" aria-hidden="true">{affectEmoji(component.family, component.label)}</span>
      <span className="affect-sticky-label">{component.label || '未命名感受'}</span>
      {primary && <span className="affect-sticky-reason">{naturalText(component.reason, '这份感受正在被真实材料慢慢描出。')}</span>}
      {primary && <span className="affect-sticky-decay">{continuityInfo(component.episode_started_at, component.active_day_continuity, component.continuity_text)}</span>}
      {primary && <span className="affect-sticky-decay">{decay.text}</span>}
    </button>
  );
}

export function StickyDeck({ components, onSelect }: { components: AffectComponent[]; onSelect: (component: AffectComponent) => void }) {
  const [expanded, setExpanded] = useState(false);
  if (!components.length) return null;
  const [dominant, ...others] = components;
  const shown = expanded ? others : others.slice(0, 2);
  const extra = Math.max(0, others.length - shown.length);
  return (
    <div className="affect-sticky-deck">
      <StickyCard component={dominant} primary={dominant.is_foreground !== false} onClick={() => onSelect(dominant)} />
      {shown.length > 0 && (
        <div className="affect-secondary-row">
          <span className="affect-connector" aria-hidden="true" />
          {shown.map(component => <StickyCard key={component.component_id} component={component} onClick={() => onSelect(component)} />)}
          {extra > 0 && <button type="button" className="affect-extra-count" onClick={() => setExpanded(true)} aria-label={`展开其余 ${extra} 份心情`}>+{extra}</button>}
          {expanded && others.length > 2 && <button type="button" className="affect-extra-count affect-extra-collapse" onClick={() => setExpanded(false)}>收起</button>}
        </div>
      )}
    </div>
  );
}

function EmptyState({ page, recognition }: { page: BoardPage; recognition?: RecognitionStatus }) {
  const waiting = page === 'resonance' && recognition?.state === 'waiting' && recognition.quiet_due_at;
  return (
    <div className="affect-empty-state">
      <span aria-hidden="true">{page === 'self' ? '🌤️' : '🌱'}</span>
      <p>{page === 'self' ? '此刻还没有可读的持久化心情。' : waiting ? '这一轮还在等待安静结算。' : '还没有仍在活跃的人类伙伴心情。'}</p>
      <small>{page === 'self' ? 'AI 回复里的 sidecar 会即时更新；漫想结束后也可能长出新的心情。' : waiting ? `持续聊天会在完整回复后分段整理；若停下来，预计 ${formatDate(recognition?.quiet_due_at)} 结算当前段。` : '识别会照常运行，不受共振传递开关影响；也可以用消息旁的心形＋主动上报。'}</small>
    </div>
  );
}

function AffectDetail({
  component,
  renderer,
  onClose,
  onRefresh,
}: {
  component: AffectComponent;
  renderer?: string;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [label, setLabel] = useState(component.label || '');
  const [family, setFamily] = useState(component.family || '复杂/未归类');
  const [reason, setReason] = useState(component.reason || '');
  const [intensity, setIntensity] = useState<IntensityChoice>(intensityChoice(component.effective_intensity ?? component.initial_intensity));
  const [persistence, setPersistence] = useState(component.persistence_class || 'situational');
  const [sourceText, setSourceText] = useState('');
  const [correctionKey] = useState(() => makeClientActionKey('correction', component.component_id));
  const [resolveKey] = useState(() => makeClientActionKey('resolve', component.component_id));
  const decay = decayInfo(component);
  const sources = sourceLabels(component);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const mutate = async (path: string, init: RequestInit, failure: string) => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}${path}`, init);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || failure);
      await onRefresh();
      onClose();
    } catch (err: any) {
      setError(err?.message || failure);
    } finally {
      setBusy(false);
    }
  };

  const correct = async () => {
    const trimmedLabel = label.trim();
    if (!trimmedLabel) { setError('请给这份感受留一个名字。'); return; }
    await mutate(`/api/affect/components/${encodeURIComponent(component.component_id)}/correct`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        label: trimmedLabel,
        family: family.trim() || '复杂/未归类',
        reason: reason.trim(),
        intensity: INTENSITY_VALUES[intensity],
        persistence,
        natural_language_form: sourceText.trim() || undefined,
        idempotency_key: correctionKey,
      }),
    }, '这次修正没有保存下来。');
  };

  const resolve = async () => {
    await mutate(`/api/affect/components/${encodeURIComponent(component.component_id)}/resolve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        natural_language_form: '人类伙伴主动结束这条情绪',
        idempotency_key: resolveKey,
      }),
    }, '这条情绪还没有结束。');
  };

  return (
    <div className="affect-detail-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="affect-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="affect-detail-title" onMouseDown={event => event.stopPropagation()}>
        <header className="affect-detail-header">
          <div><span className="affect-detail-emoji" aria-hidden="true">{affectEmoji(component.family, component.label)}</span><div><h3 id="affect-detail-title">{component.label || '未命名感受'}</h3><span className="affect-family-chip" data-family={affectFamilyTone(component.family, component.label)}>{component.family || '复杂/未归类'}</span></div></div>
          <button type="button" className="affect-icon-button" onClick={onClose} aria-label="关闭详情">✕</button>
        </header>
        <div className="affect-detail-scroll">
          <p className="affect-detail-reason">{naturalText(component.reason)}</p>
          <div className="affect-detail-grid">
            <div><span>来自</span><b>{sources.length ? sources.join('、') : '有来源的记录'}</b></div>
            <div><span>开始</span><b>{formatDate(component.started_at)}</b></div>
            <div><span>持续</span><b>{continuityInfo(component.episode_started_at, component.active_day_continuity, component.continuity_text)}</b></div>
            <div><span>最近变化</span><b>{formatDate(component.last_reinforced_at || component.updated_at)}</b></div>
            <div><span>衰减</span><b>{decay.text}</b><span className="affect-decay-track" aria-label={`情绪余韵大致还剩 ${decay.width}%`}><i style={{ width: `${decay.width}%` }} /></span></div>
          </div>
          <details className="affect-renderer-details">
            <summary>最终影响上下文的自然语言</summary>
            <p>{naturalText(renderer, '当前没有额外的自然语言情绪尾段。')}</p>
          </details>
          <button type="button" className="affect-correction-toggle" onClick={() => setCorrectionOpen(value => !value)} aria-expanded={correctionOpen}>
            <span>这里识别偏了？</span><span aria-hidden="true">{correctionOpen ? '收起' : '展开'}</span>
          </button>
          {correctionOpen && (
            <div className="affect-correction-form">
              <p className="affect-form-hint">这会留下一条人类伙伴的纠错记录，并保留原先识别作为参考。</p>
              <label>更合适的名字<input value={label} onChange={event => setLabel(event.target.value)} maxLength={120} /></label>
              <label>情绪大类<input value={family} onChange={event => setFamily(event.target.value)} maxLength={40} /></label>
              <label>一句理由<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={500} rows={3} /></label>
              <div className="affect-form-two-col">
                <label>大致强度<select value={intensity} onChange={event => setIntensity(event.target.value as IntensityChoice)}>{Object.entries(INTENSITY_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
                <label>会留多久<select value={persistence} onChange={event => setPersistence(event.target.value)}>{Object.entries(PERSISTENCE_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
              </div>
              <p className="affect-form-hint">半衰期是情绪强度自然衰减到一半的大致时间，并不是到点立刻消失。</p>
              <label>想补充的话（可选）<textarea value={sourceText} onChange={event => setSourceText(event.target.value)} maxLength={1000} rows={2} placeholder="比如：我其实是在担心，不是生气。" /></label>
              {error && <p className="affect-form-error" role="alert">{error}</p>}
              <div className="affect-form-actions"><button type="button" className="affect-secondary-button" onClick={() => setCorrectionOpen(false)} disabled={busy}>先不改</button><button type="button" className="affect-primary-button" onClick={() => void correct()} disabled={busy}>{busy ? '保存中…' : '保存这次纠正'}</button></div>
              <button type="button" className="affect-resolve-button" onClick={() => void resolve()} disabled={busy}>结束这条情绪</button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function AffectTools({
  presets,
  checkins,
  busy,
  onClose,
  onRefresh,
}: {
  presets: Preset[];
  checkins: Checkin[];
  busy: boolean;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Preset | null>(null);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const vocabulary = useMemo(() => builtinOptionsFromPresets(presets), [presets]);
  const customPresets = presets.filter(item => !isBuiltinPreset(item) && item.enabled !== false);
  const editingPreset = Boolean(draft && customPresets.some(item => item.preset_id === draft.preset_id));
  const families = [...new Set(vocabulary.map(item => item.family))];
  const labelsForFamily = (family: string) => vocabulary.filter(item => item.family === family);
  const selectedMood = vocabulary.find(item => item.family === draft?.family && item.label === draft?.label) || vocabulary[0];

  const savePreset = async () => {
    if (!draft) return;
    const reason = String(draft.reason || '').trim();
    if (!draft.label || !draft.family || !reason) { setError('请选择一份系统情绪，并写下固定理由。'); return; }
    setWorking(true); setError('');
    try {
      const dimensions = { ...(draft.dimensions || {}) };
      delete dimensions.reason;
      const response = await fetch(`${getApiBase()}/api/affect/presets/${encodeURIComponent(draft.preset_id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preset_id: draft.preset_id,
          label: draft.label,
          family: draft.family,
          emoji: selectedMood?.emoji || draft.emoji,
          default_intensity: draft.default_intensity,
          default_persistence: draft.default_persistence,
          reason,
          dimensions,
          enabled: true,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '预设没有保存下来。');
      setDraft(null); await onRefresh();
    } catch (err: any) { setError(err?.message || '预设没有保存下来。'); }
    finally { setWorking(false); }
  };
  const deletePreset = async (preset: Preset) => {
    if (isBuiltinPreset(preset)) return;
    if (!window.confirm(`删除“${preset.label} · ${presetReason(preset)}”这份有理由预设？已发生的记录会保留。`)) return;
    setWorking(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/affect/presets/${encodeURIComponent(preset.preset_id)}`, { method: 'DELETE' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '预设没有删除。');
      if (draft?.preset_id === preset.preset_id) setDraft(null);
      await onRefresh();
    } catch (err: any) { setError(err?.message || '预设没有删除。'); }
    finally { setWorking(false); }
  };
  const editCheckin = async (checkin: Checkin) => {
    const note = window.prompt('给这次记录换一句补充说明（留空也可以）', checkin.note || '');
    if (note === null) return;
    setWorking(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/affect/manual/checkins/${encodeURIComponent(checkin.checkin_id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '这次记录没有更新。');
      await onRefresh();
    } catch (err: any) { setError(err?.message || '这次记录没有更新。'); }
    finally { setWorking(false); }
  };
  const checkinAction = async (checkin: Checkin, action: 'resolve' | 'revoke' | 'delete') => {
    setWorking(true); setError('');
    try {
      const suffix = action === 'resolve' ? '/resolve' : action === 'revoke' ? '/revoke' : '';
      const response = await fetch(`${getApiBase()}/api/affect/manual/checkins/${encodeURIComponent(checkin.checkin_id)}${suffix}`, { method: action === 'delete' ? 'DELETE' : 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '这次操作没有完成。');
      await onRefresh();
    } catch (err: any) { setError(err?.message || '这次操作没有完成。'); }
    finally { setWorking(false); }
  };
  const newPreset = () => {
    const first = vocabulary[0] || BUILTIN_MOOD_OPTIONS[0];
    setDraft({
      preset_id: `manual-${Date.now()}`, label: first.label, emoji: first.emoji, family: first.family,
      default_intensity: first.default_intensity, default_persistence: first.default_persistence,
      dimensions: { ...(first.dimensions || {}) }, reason: '', enabled: true,
    });
    setError('');
  };
  const beginEdit = (preset: Preset) => {
    if (isBuiltinPreset(preset)) return;
    const mood = vocabulary.find(item => item.family === preset.family && item.label === preset.label) || vocabulary[0];
    setDraft({
      ...preset,
      family: mood?.family || preset.family,
      label: mood?.label || preset.label,
      emoji: mood?.emoji || preset.emoji,
      dimensions: { ...(mood?.dimensions || {}), ...(preset.dimensions || {}) },
      reason: presetReason(preset),
    });
    setError('');
  };
  const updateFamily = (family: string) => {
    const first = labelsForFamily(family)[0];
    if (!draft || !first) return;
    setDraft({ ...draft, family, label: first.label, emoji: first.emoji, dimensions: { ...(first.dimensions || {}) } });
  };
  const updateLabel = (label: string) => {
    const mood = labelsForFamily(draft?.family || '').find(item => item.label === label);
    if (!draft || !mood) return;
    setDraft({ ...draft, label: mood.label, emoji: mood.emoji, dimensions: { ...(draft.dimensions || {}), ...(mood.dimensions || {}) } });
  };
  return (
    <div className="affect-tools-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="affect-tools-dialog" role="dialog" aria-modal="true" aria-labelledby="affect-tools-title" onMouseDown={event => event.stopPropagation()}>
        <header className="affect-detail-header"><div><span className="affect-detail-emoji" aria-hidden="true">⚙️</span><div><h3 id="affect-tools-title">管理心情预设</h3><span className="affect-tools-subtitle">系统情绪词库不可编辑；这里只管理人类伙伴自己的固定理由。</span></div></div><button type="button" className="affect-icon-button" onClick={onClose} aria-label="关闭心情预设管理">✕</button></header>
        <div className="affect-tools-scroll">
          {!draft ? <div className="affect-tools-section">
            <div className="affect-tools-section-head"><div><h4>带原因的预设</h4><p className="affect-form-hint">选择系统已有的情绪标签，再写一条固定理由；快捷上报时只能选择，不会临时输入。</p></div></div>
            <button type="button" className="affect-preset-create" onClick={newPreset} disabled={busy || working}><span aria-hidden="true">＋</span><span><b>新增心情预设</b><small>从一张空白便签开始</small></span></button>
            <div className="affect-tools-presets">{customPresets.map(preset => <div className="affect-tool-preset" key={preset.preset_id}><span className="affect-tool-preset-label"><span className="affect-tool-preset-emoji">{preset.emoji}</span><span className="affect-tool-preset-copy"><b>{preset.label}</b><small>{presetReason(preset)}</small></span></span><button type="button" className="affect-tool-edit" onClick={() => beginEdit(preset)} disabled={busy || working} aria-label={`编辑${preset.label}预设`}>✎ 编辑</button></div>)}</div>
            {!customPresets.length && <p className="affect-tools-empty">还没有自定义理由预设；可以新增一份，例如“烦躁 · 上班开太多会”。</p>}
          </div> : <div className="affect-tools-section affect-preset-editor-section">
            <div className="affect-preset-editor-head"><div><span>{editingPreset ? '正在编辑' : '正在新增'}</span><h4>{editingPreset ? `编辑「${draft.label}」预设` : '创建一份新预设'}</h4><p>{editingPreset ? '只会修改这一张已有便签。' : '保存后会新增一张带固定理由的便签。'}</p></div><button type="button" className="affect-secondary-button" onClick={() => setDraft(null)} disabled={working}>返回列表</button></div>
            <div className="affect-preset-form">
              <div className="affect-preset-preview"><span aria-hidden="true">{selectedMood?.emoji || draft.emoji}</span><span><b>{draft.label || '选择一种心情'}</b><small>{draft.reason?.trim() || '固定理由会显示在这里'}</small></span></div>
              <label>情绪大类<select value={draft.family} onChange={event => updateFamily(event.target.value)}>{families.map(family => <option key={family} value={family}>{family}</option>)}</select></label>
              <label>具体情绪<select value={draft.label} onChange={event => updateLabel(event.target.value)}>{labelsForFamily(draft.family).map(item => <option key={item.label} value={item.label}>{item.emoji} {item.label}</option>)}</select></label>
              <label>固定理由（快捷上报时使用）<textarea value={draft.reason || ''} onChange={event => setDraft({ ...draft, reason: event.target.value })} maxLength={500} rows={3} placeholder="例如：上班开太多会" /></label>
              <div className="affect-form-two-col"><label>默认强度<select value={intensityChoice(draft.default_intensity)} onChange={event => setDraft({ ...draft, default_intensity: INTENSITY_VALUES[event.target.value as IntensityChoice] })}>{Object.entries(INTENSITY_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><label>自然衰减<select value={draft.default_persistence} onChange={event => setDraft({ ...draft, default_persistence: event.target.value })}>{Object.entries(PERSISTENCE_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label></div>
              <p className="affect-form-hint">半衰期是强度衰减到一半的大致时间，不是到点立刻消失；后续事件仍可能让它增强、演变或提前闭环。</p>
              <div className="affect-form-actions">{editingPreset && <button type="button" className="affect-resolve-button" onClick={() => void deletePreset(draft)} disabled={working}>删除这份预设</button>}<button type="button" className="affect-primary-button" onClick={() => void savePreset()} disabled={working || !draft.reason?.trim()}>{working ? '保存中…' : editingPreset ? '保存这次修改' : '创建新预设'}</button></div>
            </div>
          </div>}
          <div className="affect-tools-section"><div className="affect-tools-section-head"><h4>最近的人工记录</h4><span className="affect-tools-subtitle">只在这个二级区域显示</span></div>{checkins.slice(0, 12).map(checkin => <div className="affect-history-item" key={`${checkin.checkin_id}:${checkin.revision}`}><div><b>{checkin.emoji} {checkin.label}</b><small>{formatDate(checkin.created_at)}</small>{checkin.note && <p>{naturalText(checkin.note)}</p>}</div><div className="affect-history-actions"><button type="button" onClick={() => void editCheckin(checkin)} disabled={working}>改一句</button><button type="button" onClick={() => void checkinAction(checkin, 'resolve')} disabled={working || checkin.status !== 'active'}>结束</button><button type="button" onClick={() => void checkinAction(checkin, 'revoke')} disabled={working || checkin.status !== 'active'}>撤回</button><button type="button" onClick={() => void checkinAction(checkin, 'delete')} disabled={working}>删除</button></div></div>)}{!checkins.length && <p className="affect-tools-empty">还没有人工记录。</p>}</div>
          {error && <p className="affect-form-error" role="alert">{error}</p>}
        </div>
      </section>
    </div>
  );
}

type AffectQuickCheckinProps = {
  open: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
};

/**
 * 人类伙伴面向 AI 的唯一快捷上报入口。
 * 预设读取和真正 POST 都在这里完成；AffectBoard 只负责查看和管理，
 * 避免用户在监控/管理页面误触产生一条人工记录。
 */
export function AffectQuickCheckin({ open, onClose, onSubmitted }: AffectQuickCheckinProps) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetchAffect('/api/affect/board', { cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '暂时读不到心情预设。');
      const items: Preset[] = (Array.isArray(body.presets) ? body.presets : []).filter((item: any): item is Preset => item && item.enabled !== false);
      setPresets(items);
      const keys = [
        ...builtinOptionsFromPresets(items).map(item => `builtin:${item.preset_id}`),
        ...items.filter(item => !isBuiltinPreset(item) && presetReason(item)).map(item => `preset:${item.preset_id}`),
      ];
      setSelectedKey(current => keys.includes(current) ? current : '');
    } catch (err: any) {
      setError(err?.message || '暂时读不到心情预设。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      setSuccess(false);
      setError('');
      setSelectedKey('');
      return;
    }
    void load();
  }, [open, load]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape' && !submitting) onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose, submitting]);

  if (!open) return null;

  const builtins = builtinOptionsFromPresets(presets);
  const customPresets = presets.filter(item => !isBuiltinPreset(item) && presetReason(item));
  const grouped = builtins.reduce<Record<string, Preset[]>>((result, item) => {
    (result[item.family] ||= []).push(item);
    return result;
  }, {});
  const selectedKind = selectedKey.startsWith('builtin:') ? 'builtin' : selectedKey.startsWith('preset:') ? 'preset' : '';
  const selectedId = selectedKey.replace(/^(builtin|preset):/, '');
  const selected = selectedKind === 'builtin'
    ? builtins.find(item => item.preset_id === selectedId)
    : customPresets.find(item => item.preset_id === selectedId);
  const selectedReason = selected ? (selectedKind === 'builtin' ? `人类伙伴现在感到${selected.label}，但不知道具体原因` : presetReason(selected)) : '';
  const preview = selected ? `${selected.label} · ${selectedReason}` : '请选择一份心情';

  const submit = async () => {
    if (!selected || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      const payload = selectedKind === 'preset'
        ? { preset_id: selected.preset_id }
        : {
          label: selected.label,
          family: selected.family,
          emoji: selected.emoji,
          intensity: selected.default_intensity,
          persistence: selected.default_persistence,
          dimensions: selected.dimensions,
        };
      const response = await fetch(`${getApiBase()}/api/affect/manual/checkins`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '这次心情没有告诉 AI。');
      setSuccess(true);
      onSubmitted?.();
    } catch (err: any) {
      setError(err?.message || '网络没有把这次心情送出去。可以重试，也可以先关闭。');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="affect-quick-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget && !submitting) onClose(); }}>
      <section className="affect-quick-dialog" role="dialog" aria-modal="true" aria-labelledby="affect-quick-title" onMouseDown={event => event.stopPropagation()}>
        <header className="affect-quick-header">
          <div><span className="affect-quick-mark" aria-hidden="true">♡+</span><div><h2 id="affect-quick-title">告诉 AI 你的心情</h2><p>这里只选择已有的情绪或固定理由预设，不临时填写。</p></div></div>
          <button type="button" className="affect-icon-button" onClick={onClose} disabled={submitting} aria-label="关闭快速报告心情">✕</button>
        </header>
        {success ? (
          <div className="affect-quick-success"><span aria-hidden="true">💌</span><h3>收到了，AI 会记得这一刻。</h3><p>{preview}</p><button type="button" className="affect-primary-button" onClick={onClose}>好，收好</button></div>
        ) : (
          <div className="affect-quick-scroll">
            {error && <div className="affect-board-error" role="alert"><span>{error}</span><span className="affect-quick-error-actions"><button type="button" onClick={() => void (selected ? submit() : load())} disabled={loading || submitting}>重试</button><button type="button" onClick={() => setError('')} aria-label="关闭提示">×</button></span></div>}
            {loading ? <div className="affect-board-loading">正在读取你的心情预设…</div> : !builtins.length && !customPresets.length ? <div className="affect-quick-empty"><p>还没有可用的心情预设。</p><small>可以先在 Affect 的“管理心情预设”里新增一份。</small></div> : (
              <>
                <fieldset className="affect-quick-presets">
                  <legend>只报心情</legend>
                  <p className="affect-quick-group-hint">系统内置词库，按情绪大类整理；不会改动或删除。</p>
                  {Object.entries(grouped).map(([family, items]) => (
                    <details className="affect-quick-family" key={family} open>
                      <summary>{family}<span>{items.length}</span></summary>
                      <div className="affect-quick-family-options">{items.map(item => {
                        const key = `builtin:${item.preset_id}`;
                        return <label key={key} className={`affect-quick-preset${selectedKey === key ? ' selected' : ''}`}><input type="radio" name="affect-quick-preset" value={key} checked={selectedKey === key} onChange={() => setSelectedKey(key)} /><span className="affect-quick-preset-emoji" aria-hidden="true">{item.emoji}</span><span>{item.label}</span></label>;
                      })}</div>
                    </details>
                  ))}
                </fieldset>
                <fieldset className="affect-quick-presets affect-quick-reason-presets">
                  <legend>带原因的预设</legend>
                  <p className="affect-quick-group-hint">人类伙伴在「心情」面板维护的固定理由；快捷上报时不会临时改写。</p>
                  {customPresets.length ? customPresets.map(item => {
                    const key = `preset:${item.preset_id}`;
                    return <label key={key} className={`affect-quick-preset affect-quick-reason-preset${selectedKey === key ? ' selected' : ''}`}><input type="radio" name="affect-quick-preset" value={key} checked={selectedKey === key} onChange={() => setSelectedKey(key)} /><span className="affect-quick-preset-emoji" aria-hidden="true">{item.emoji}</span><span><b>{item.label}</b><small>{presetReason(item)}</small></span></label>;
                  }) : <p className="affect-quick-empty affect-quick-inline-empty">还没有带原因的预设；可以在「心情」面板新增。</p>}
                </fieldset>
                <div className="affect-quick-preview"><span>预览</span><b>{preview}</b></div>
                <div className="affect-quick-actions"><button type="button" className="affect-secondary-button" onClick={onClose} disabled={submitting}>先不说</button><button type="button" className="affect-primary-button" onClick={() => void submit()} disabled={!selected || submitting}>{submitting ? '告诉 AI 中…' : '确认告诉 AI'}</button></div>
              </>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

export default function AffectBoard({ open, onClose }: Props) {
  const [page, setPage] = useState<BoardPage>('self');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<AffectComponent | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const response = await fetchAffect('/api/affect/board', { cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
      setOverview(body);
    } catch (err: any) {
      setError(err?.message || '现在还读不到心情纸张。');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!open) { setSelected(null); setToolsOpen(false); setHelpOpen(false); return; }
    void load();
    const refresh = window.setInterval(() => { void load(); }, 30 * 1000);
    return () => window.clearInterval(refresh);
  }, [open, load]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || selected || toolsOpen) return;
      if (helpOpen) setHelpOpen(false);
      else onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, selected, toolsOpen, helpOpen, onClose]);

  const projection = overview?.projection || {};
  const settings = overview?.settings || {};
  const state = page === 'self' ? projection.self : page === 'resonance' ? projection.resonance : undefined;
  const components = state?.components || [];
  const presets = useMemo(() => overview?.presets || [], [overview?.presets]);
  const checkins = useMemo(() => overview?.checkins || [], [overview?.checkins]);
  const channelEnabled = settings.resonance_channel_enabled === true;
  const recognition = overview?.recognition || {};
  const renderer = page === 'self' ? projection.renderer_self : projection.renderer_resonance_display;
  const contextRenderer = page === 'self' ? projection.renderer_self : projection.renderer_resonance;
  const recognitionText = (() => {
    if (recognition.enabled === false || recognition.state === 'paused') return '识别已暂停';
    if (recognition.state === 'analyzing') return '正在整理最近的表达';
    if (recognition.state === 'waiting') return `聊天中约每 ${recognition.active_checkpoint_minutes || 10} 分钟或 ${recognition.active_checkpoint_user_messages || 8} 条消息分段更新；停聊 ${recognition.quiet_minutes || 30} 分钟结算`;
    if (recognition.state === 'failed') return `上次识别没有完成${recognition.last_error ? ` · ${recognition.last_error}` : ''}`;
    if (recognition.state === 'ready') return `识别正常${recognition.last_success_at ? ` · 最近更新 ${formatDate(recognition.last_success_at)}` : ''}`;
    return '等待下一段真实表达';
  })();
  const boardMark = page === 'open_loops' ? '🪶' : page === 'self'
    ? (state?.dominant ? affectEmoji(state.dominant.family, state.dominant.label) : '🌤️')
    : '🌱';

  const toggleChannel = async () => {
    const next = !channelEnabled;
    setBusy(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/affect/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ resonance_channel_enabled: next }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '共振通道没有更新。');
      await load();
    } catch (err: any) { setError(err?.message || '共振通道没有更新。'); }
    finally { setBusy(false); }
  };

  if (!open) return null;
  return (
    <>
      <div className="affect-board-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
        <section className="affect-board-modal" role="dialog" aria-modal="true" aria-labelledby="affect-board-title" onMouseDown={event => event.stopPropagation()}>
          <header className="affect-board-header"><div><span className="affect-board-mark" aria-hidden="true">{boardMark}</span><div><div className="affect-board-title-line"><h2 id="affect-board-title">{page === 'self' ? 'AI 的心情' : page === 'resonance' ? '人类伙伴共振' : '未完心绪'}</h2>{page !== 'open_loops' && <button type="button" className="affect-help-button" aria-label="查看心情图标说明" aria-expanded={helpOpen} onClick={() => setHelpOpen(value => !value)}>?</button>}</div><p>{page === 'open_loops' ? '看见 AI 的牵挂如何留在眼前、退到背景，或自然放下。' : rendererPreview(renderer)}</p></div></div><button type="button" className="affect-icon-button" onClick={onClose} aria-label="关闭心情纸张">✕</button></header>
          <div className="affect-board-scroll">
            <nav className="affect-board-tabs" role="tablist" aria-label="心情页面"><button type="button" role="tab" aria-selected={page === 'self'} className={page === 'self' ? 'active' : ''} onClick={() => setPage('self')}>AI 的心情</button><button type="button" role="tab" aria-selected={page === 'resonance'} className={page === 'resonance' ? 'active' : ''} onClick={() => setPage('resonance')}>人类伙伴共振</button><button type="button" role="tab" aria-selected={page === 'open_loops'} className={page === 'open_loops' ? 'active' : ''} onClick={() => setPage('open_loops')}>未完心绪</button></nav>
            {helpOpen && <AffectEmojiHelp onClose={() => setHelpOpen(false)} />}
            {error && <div className="affect-board-error" role="alert">{error}<button type="button" onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
            {loading && !overview ? <div className="affect-board-loading">正在把这块小小的情绪纸张铺开…</div> : <>
              {page === 'resonance' && <div className={`affect-channel-pill ${channelEnabled ? 'on' : 'off'}`}><div className="affect-channel-copy"><span><i className="affect-recognition-dot" aria-hidden="true" /><b>人类伙伴心情识别</b><small>{recognitionText}</small></span><span><i className="affect-channel-dot" aria-hidden="true" /><b>{channelEnabled ? '共振传递已打开' : '共振传递已关闭'}</b><small>{channelEnabled ? '识别结果会进入 AI 的上下文' : '只在这里展示，不会进入 AI 的上下文'}</small></span></div><button type="button" onClick={() => void toggleChannel()} disabled={busy} aria-pressed={channelEnabled}>{channelEnabled ? '关闭传递' : '打开传递'}</button></div>}
              {page === 'open_loops' ? <OpenLoopLifecycle /> : components.length ? <StickyDeck components={components} onSelect={setSelected} /> : <EmptyState page={page} recognition={recognition} />}
              {page === 'resonance' && <div className="affect-management-card"><div><span className="affect-checkin-icon" aria-hidden="true">⚙️</span><div><b>管理心情预设</b><small>预设只在这里编辑、新增或隐藏；真正上报请点人类伙伴消息头像下方的心形＋。</small></div></div><button type="button" className="affect-secondary-button" onClick={() => setToolsOpen(true)} disabled={busy}>打开管理</button></div>}
            </>}
          </div>
          <footer className="affect-board-footer">{page === 'self' ? '当前活跃日的前景心情参与主位；跨日余韵和轻微感受保留在背景，以无理由的短描述进入上下文。' : page === 'resonance' ? '识别与共振传递彼此独立；关闭传递也能在这里检查识别结果。' : '未完心绪由 Open Loop 独立保存；这里仅展示生命周期，不把它当作情绪或记忆事实。'}</footer>
        </section>
      </div>
      {selected && <AffectDetail component={selected} renderer={contextRenderer} onClose={() => setSelected(null)} onRefresh={load} />}
      {toolsOpen && <AffectTools presets={presets} checkins={checkins} busy={busy} onClose={() => setToolsOpen(false)} onRefresh={load} />}
    </>
  );
}
