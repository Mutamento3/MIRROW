import { useState, useRef, useEffect } from 'react';
import { getApiBase } from '../config';
import { useStatusPlace } from '../hooks/useStatusPlace';
import { toHierarchicalLabel, type StatusMeta } from '../utils/userStatusLabel';

// behavior_mode → 中文 tag
function modeTag(meta?: StatusMeta | null): string {
  if (!meta) return '';
  const mode = meta.rest_mode === 'sleeping' || meta.rest_mode === 'napping' ? 'rest'
    : meta.activity_category === 'bathing' ? 'rest'
    : meta.location === 'out' ? 'away'
    : 'home';
  return mode === 'rest' ? '休息' : mode === 'away' ? '外出' : '在家';
}

// 在家/外出 分组
const HOME_STATUSES = ['gaming', 'coding', 'bathing', 'eating', 'napping', 'sleeping', 'idle', 'other'];
const OUT_STATUSES = ['out'];

const ALL_OPTIONS: { value: string; label: string; group: 'home' | 'out' | 'rest' }[] = [
  ...HOME_STATUSES.map(v => ({ value: v, label: toHierarchicalLabel(v), group: 'home' as const })),
  ...OUT_STATUSES.map(v => ({ value: v, label: toHierarchicalLabel(v), group: 'out' as const })),
];

export default function UserStatusPanel({ currentStatus, onStatusChange, savedCustomText, presets, mode, statusMeta }: {
  currentStatus: string;
  onStatusChange: (status: string, customText: string) => void;
  savedCustomText?: string;
  presets?: Record<string, string[]>;
  mode?: string;
  statusMeta?: StatusMeta | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [customText, setCustomText] = useState(savedCustomText || '');
  const [showPresets, setShowPresets] = useState(false);
  const [statusLog, setStatusLog] = useState<any[]>([]);
  const [showStatusLog, setShowStatusLog] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const fetchStatusLog = async () => {
    try {
      const today = new Date().toISOString().slice(0, 10);
      const res = await fetch(`${getApiBase()}/api/health/status-log?date=${today}`);
      const data = await res.json();
      setStatusLog(data.entries || []);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    setCustomText(savedCustomText || '');
  }, [savedCustomText]);

  const currentStatusRef = useRef(currentStatus);
  currentStatusRef.current = currentStatus;
  const customTextRef = useRef(customText);
  customTextRef.current = customText;

  // Debounced sync of custom text changes
  useEffect(() => {
    const status = currentStatusRef.current;
    if (status === 'idle' || !status) return;
    const timer = setTimeout(() => {
      fetch(`${getApiBase()}/api/wander/user-status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: currentStatusRef.current, custom_text: customTextRef.current })
      }).catch(e => console.error('更新描述失败:', e));
    }, 400);
    return () => clearTimeout(timer);
  }, [customText]);

  // beforeunload: flush pending data
  useEffect(() => {
    const onUnload = () => {
      const status = currentStatusRef.current;
      if (status === 'idle' || !status) return;
      const blob = new Blob([JSON.stringify({ status, custom_text: customTextRef.current })],
        { type: 'application/json' });
      navigator.sendBeacon(`${getApiBase()}/api/wander/user-status`, blob);
    };
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, []);

  // 层级标签
  const place = useStatusPlace();
  const visiblePlace = statusMeta ? statusMeta.location === 'out' : currentStatus === 'out';
  const hierarchicalLabel = toHierarchicalLabel(currentStatus, statusMeta, visiblePlace ? place?.label : undefined);
  const tag = modeTag(statusMeta);

  // auto 模式只暴露 4 个铁证状态，但标签用层级格式
  const autoModeKeys = new Set(['gaming', 'out', 'sleeping', 'idle']);
  const statusOptions = mode === 'auto'
    ? ALL_OPTIONS.filter(o => autoModeKeys.has(o.value)).map(o =>
        o.value === 'idle' ? { ...o, label: statusMeta ? toHierarchicalLabel('idle', statusMeta) : '❓ 未知' } : o)
    : ALL_OPTIONS;

  // manual 模式分组
  const homeOptions = statusOptions.filter(o => HOME_STATUSES.includes(o.value));
  const outOptions = statusOptions.filter(o => OUT_STATUSES.includes(o.value));

  const currentPresets: string[] = presets?.[currentStatus] || [];
  const detailText = (currentStatus === 'idle' || !currentStatus || !customText)
    ? ''
    : ` · ${customText}`;

  const handleStatusClick = (status: string) => {
    const isStale = (customText === savedCustomText && customText !== '');
    const textToSend = isStale ? '' : customText;
    if (isStale) setCustomText('');
    onStatusChange(status, textToSend);
    setTimeout(() => fetchStatusLog(), 500);
  };

  const handlePresetPick = (preset: string) => {
    setCustomText(preset);
    onStatusChange(currentStatus, preset);
    setShowPresets(false);
  };

  // status log 中的条目也转为层级标签
  const formatLogStatus = (s: string) => {
    // 尝试从 statusMeta 获取 location 来构建层级标签
    // 注意：历史条目没有 meta，仅用 legacy 显示
    const oldMap: Record<string, string> = {
      gaming: '🎮 在家·游戏', out: '🚪 外出', bathing: '🛁 在家·洗澡',
      eating: '🍽 在家·吃饭', napping: '😴 在家·小憩', sleeping: '🌙 在家·睡眠',
      idle: '💤 在家·空闲', other: '📝 在家·其他', coding: '💻 在家·代码',
    };
    return oldMap[s] || s;
  };

  return (
    <div className="sidebar-section">
      <h3>用户状态</h3>

      {/* 折叠栏 — 层级标签 */}
      <div className={`user-status-bar ${expanded ? 'expanded' : ''}`} title={visiblePlace ? place?.detail : undefined} onClick={() => setExpanded(!expanded)}>
        <span className="user-status-bar-label">
          当前状态：{hierarchicalLabel}{detailText}
          {tag && <span style={{fontSize:'0.65em',opacity:0.7,marginLeft:4}}>[{tag}]</span>}
          {statusMeta?.location === 'home' && <span style={{marginLeft:4}}>🏠</span>}
          {statusMeta?.location === 'out' && <span style={{marginLeft:4}}>🚪</span>}
        </span>
        <span className="user-status-bar-arrow">{expanded ? '△' : '▽'}</span>
      </div>

      {/* 展开面板 */}
      {expanded && (
        <div className="user-status-panel">
          {visiblePlace && place && (
            <div style={{fontSize:'0.8em',color:'var(--text-secondary)',marginBottom:6}}>
              📍 {place.detail}
            </div>
          )}
          {/* auto 模式：顶部显示引擎推断标签 */}
          {mode === 'auto' && statusMeta && statusMeta.source === 'auto' && (
            <div style={{fontSize:'0.8em',color:'var(--text-secondary)',marginBottom:6}}>
              🤖 引擎推断：{hierarchicalLabel}
              {statusMeta.location === 'home' && ' 🏠'}
              {statusMeta.location === 'out' && ' 🚪'}
            </div>
          )}

          {/* manual 模式：分组显示 */}
          {mode !== 'auto' ? (
            <>
              <div style={{fontSize:'0.8em',fontWeight:600,marginBottom:4,color:'var(--text-secondary)'}}>🏠 在家</div>
              <div className="user-status-selector">
                {homeOptions.map(opt => (
                  <button
                    key={opt.value}
                    className={`user-status-btn ${currentStatus === opt.value ? 'active' : ''}`}
                    onClick={(e) => { e.stopPropagation(); handleStatusClick(opt.value); }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <div style={{fontSize:'0.8em',fontWeight:600,margin:'8px 0 4px',color:'var(--text-secondary)'}}>🚪 外出</div>
              <div className="user-status-selector">
                {outOptions.map(opt => (
                  <button
                    key={opt.value}
                    className={`user-status-btn ${currentStatus === opt.value ? 'active' : ''}`}
                    onClick={(e) => { e.stopPropagation(); handleStatusClick(opt.value); }}
                  >
                    {currentStatus === 'out' && opt.value === 'out' ? hierarchicalLabel : opt.label}
                  </button>
                ))}
              </div>
            </>
          ) : (
            /* auto 模式：4 个铁证按钮 */
            <div className="user-status-selector">
              {statusOptions.map(opt => (
                <button
                  key={opt.value}
                  className={`user-status-btn ${currentStatus === opt.value ? 'active' : ''}`}
                  onClick={(e) => { e.stopPropagation(); handleStatusClick(opt.value); }}
                >
                  {currentStatus === 'out' && opt.value === 'out' ? hierarchicalLabel : opt.label} <span style={{fontSize:'0.65em',opacity:0.7}}>[{tag || '在家'}]</span>
                </button>
              ))}
            </div>
          )}

          {/* location 指示器（manual 模式下传感器仍提供参考） */}
          {mode !== 'auto' && statusMeta && (
            <div style={{fontSize:'0.75em',color:'var(--text-secondary)',marginTop:4}}>
              📡 传感器推断：{statusMeta.location === 'home' ? '在家 🏠' : statusMeta.location === 'out' ? `${place?.label || '外出'} 🚪` : '未知'}
              {statusMeta.source === 'auto' && '（自动）'}
            </div>
          )}

          {/* 自定义文本输入 */}
          {currentStatus !== 'idle' && currentStatus && (
            <div className="user-status-input-wrapper">
              <input
                ref={inputRef}
                type="text"
                className="user-status-input"
                placeholder="输入描述（最多20字）"
                maxLength={20}
                value={customText}
                onChange={(e) => setCustomText(e.target.value.slice(0, 20))}
                onFocus={() => { if (currentPresets.length > 0) setShowPresets(true); }}
                onBlur={() => setTimeout(() => setShowPresets(false), 200)}
              />
              {showPresets && currentPresets.length > 0 && (
                <div className="user-status-presets">
                  {currentPresets.map(p => (
                    <button
                      key={p}
                      className="user-status-preset-btn"
                      onMouseDown={(e) => { e.preventDefault(); handlePresetPick(p); }}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 切换日志 */}
          <div style={{marginTop:8,fontSize:'0.75em'}}>
            <button
              onClick={() => { setShowStatusLog(!showStatusLog); if (!showStatusLog) fetchStatusLog(); }}
              style={{background:'none',border:'none',color:'var(--text-secondary)',cursor:'pointer',padding:'2px 4px',fontSize:'inherit'}}
            >📋 今日切换记录 {showStatusLog ? '▲' : '▼'}</button>
            {showStatusLog && (
              <div style={{maxHeight:150,overflowY:'auto',marginTop:4,padding:'4px 6px',background:'rgba(0,0,0,0.03)',borderRadius:4}}>
                {statusLog.length === 0 ? (
                  <span style={{color:'var(--text-secondary)'}}>暂无记录</span>
                ) : (
                  statusLog.map((entry: any, i: number) => (
                    <div key={i} style={{padding:'2px 0',borderBottom:'1px solid rgba(255,255,255,0.05)'}}>
                      <span style={{marginRight:6,color:'var(--text-secondary)'}}>{entry.timestamp?.slice(11, 16) || ''}</span>
                      <span>{formatLogStatus(entry.from_status)} → {formatLogStatus(entry.to_status)}</span>
                      {entry.custom_text && <span style={{marginLeft:4,color:'var(--text-secondary)'}}>({entry.custom_text})</span>}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
