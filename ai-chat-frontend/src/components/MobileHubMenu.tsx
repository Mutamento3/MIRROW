import type { ReactNode } from 'react';

/**
 * Pages available from the mobile hub. Keep this union aligned with App's
 * top-level view state so the desktop navigation remains the source of truth.
 */
export type MobileView =
  | 'chat'
  | 'persona'
  | 'monitor'
  | 'affect'
  | 'memory'
  | 'events'
  | 'social'
  | 'machineLife'
  | 'workshop'
  | 'lounge'
  | 'worldbook'
  | 'selfbook';

type HubItem = {
  view: MobileView;
  icon: string;
  label: string;
  description: string;
};

const HUB_ITEMS: HubItem[] = [
  { view: 'chat', icon: '💬', label: '聊天', description: '回到主聊天' },
  { view: 'persona', icon: '🎭', label: '人设卡', description: 'AI 的人格与头像' },
  { view: 'monitor', icon: '📊', label: '监控台', description: '感知与运行状态' },
  { view: 'affect', icon: '🌤️', label: '心情', description: 'AI 当前与人类伙伴共振' },
  { view: 'memory', icon: '🧠', label: '记忆库', description: '记忆、待办与档案' },
  { view: 'events', icon: '📅', label: '事件纪', description: '日记与重要事件' },
  { view: 'machineLife', icon: '✳', label: '机生活', description: '社交 · 游戏 · 媒体' },
  { view: 'selfbook', icon: '📖', label: '认知书', description: '自我、他者与世界' },
];

export interface MobileHubMenuProps {
  open: boolean;
  view: MobileView;
  onNavigate: (view: MobileView) => void;
  onClose: () => void;
  onOpenTasks: () => void;
  
  onOpenSettings: () => void;
  onToggleNight: () => void | Promise<void>;
  onToggleMusic: () => void;
  onTogglePhoneBrowse: () => void | Promise<void>;
  nightMode: boolean;
  nightModePending?: boolean;
  phoneBrowseModePending?: boolean;
  musicMode: boolean;
  phoneBrowseMode: boolean;
  
}

function HubIcon({ children }: { children: ReactNode }) {
  return <span className="mobile-hub-item-icon" aria-hidden="true">{children}</span>;
}

/**
 * A mobile-only bottom sheet for page navigation and the controls that would
 * otherwise crowd the header. It deliberately owns no application state:
 * App keeps the same callbacks used by the desktop controls.
 */
export default function MobileHubMenu({
  open,
  view,
  onNavigate,
  onClose,
  onOpenTasks,
  onOpenSettings,
  onToggleNight,
  onToggleMusic,
  onTogglePhoneBrowse,
  nightMode,
  nightModePending = false,
  phoneBrowseModePending = false,
  musicMode,
  phoneBrowseMode,
}: MobileHubMenuProps) {
  if (!open) return null;

  const runAndClose = (action: () => void | Promise<void>) => {
    onClose();
    void action();
  };

  return (
    <>
      <div
        className="mobile-hub-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <section
        className="mobile-hub-menu"
        role="dialog"
        aria-modal="true"
        aria-label="MIRROW 手机中枢"
      >
        <div className="mobile-hub-grabber" aria-hidden="true" />
        <header className="mobile-hub-header">
          <div>
            <p className="mobile-hub-kicker">MIRROW · 手机中枢</p>
            <h2>去哪里，或要做什么？</h2>
          </div>
          <button
            type="button"
            className="mobile-hub-close"
            onClick={onClose}
            aria-label="关闭手机中枢"
          >
            ×
          </button>
        </header>

        <div className="mobile-hub-section-title">页面入口</div>
        <nav className="mobile-hub-grid" aria-label="页面入口">
          {HUB_ITEMS.map(item => (
            <button
              type="button"
              key={item.view}
              className={`mobile-hub-item${view === item.view || (item.view === 'machineLife' && ['social', 'lounge'].includes(view)) ? ' active' : ''}`}
              onClick={() => {
                onNavigate(item.view);
                onClose();
              }}
              aria-current={view === item.view ? 'page' : undefined}
            >
              <HubIcon>{item.icon}</HubIcon>
              <span className="mobile-hub-item-copy">
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="mobile-hub-section-title">快捷控制</div>
        <div className="mobile-hub-controls" aria-label="快捷控制">
          
          <button type="button" className="mobile-hub-control" onClick={() => runAndClose(onOpenTasks)}>
            <HubIcon>📋</HubIcon>
            <span>任务</span>
          </button>
          <button type="button" className="mobile-hub-control" onClick={() => runAndClose(onOpenSettings)}>
            <HubIcon>⚙️</HubIcon>
            <span>设置</span>
          </button>
          <button
            type="button"
            className={`mobile-hub-control${nightMode ? ' active' : ''}`}
            onClick={() => runAndClose(onToggleNight)}
            disabled={nightModePending}
            aria-pressed={nightMode}
          >
            <HubIcon>{nightMode ? '❤️‍🔥' : '🤍'}</HubIcon>
            <span>{nightMode ? '深夜中' : '深夜'}</span>
          </button>
          <button
            type="button"
            className={`mobile-hub-control${musicMode ? ' active' : ''}`}
            onClick={() => runAndClose(onToggleMusic)}
            aria-pressed={musicMode}
          >
            <HubIcon>🎵</HubIcon>
            <span>{musicMode ? '一起听 · 中枢' : '音乐中枢'}</span>
          </button>
          <button
            type="button"
            className={`mobile-hub-control${phoneBrowseMode ? ' active' : ''}`}
            onClick={() => runAndClose(onTogglePhoneBrowse)}
            disabled={phoneBrowseModePending}
            aria-pressed={phoneBrowseMode}
          >
            <HubIcon>📱</HubIcon>
            <span>{phoneBrowseMode ? '刷手机中' : '刷手机'}</span>
          </button>
        </div>
      </section>
    </>
  );
}
