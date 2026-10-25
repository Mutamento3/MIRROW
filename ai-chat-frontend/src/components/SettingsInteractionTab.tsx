import React, { useState, useEffect } from 'react';
import { getApiBase, getPlatform } from '../config';

/**
 * 互动级别设置页 — 控制 AI 的主动性。
 * - 漫想模式总开关（持久化，即时生效）
 * - 硅基感知总开关（持久化，即时生效，复用哨兵 toggle）
 * - 漫想手动测试（运行时开/关 + 状态）
 * - 刷手机模式截图间隔
 */
interface WanderStatusLite {
  mode?: string;
  creator_running?: boolean;
  idle_seconds?: number;
  idle_threshold?: number;
  user_status_display?: string;
  user_status_custom_text?: string;
}

interface Props {
  wanderStatus: WanderStatusLite | null;
  fetchWanderStatus: () => void;
  phoneBrowseInterval: number;
  setPhoneBrowseInterval: (v: number) => void;
  phoneBrowseMode: boolean;
  widgetDiag: string;
  showToast?: (type: string, msg: string) => void;
}

export const SettingsInteractionTab: React.FC<Props> = ({
  wanderStatus, fetchWanderStatus, phoneBrowseInterval, setPhoneBrowseInterval,
  phoneBrowseMode, widgetDiag, showToast,
}) => {
  const [wanderEnabled, setWanderEnabled] = useState(true);
  const [sentinelEnabled, setSentinelEnabled] = useState(true);
  const [listenMusicPlayback, setListenMusicPlayback] = useState(false);
  const [listenMusicPlaybackDevice, setListenMusicPlaybackDevice] = useState<'computer'|'mobile'>('computer');
  const toast = (t: string, m: string) => showToast ? showToast(t, m) : undefined;

  // 读当前状态：漫想 enabled 存 settings；哨兵 enabled 走 /api/sentinel/status
  useEffect(() => {
    fetch(`${getApiBase()}/api/settings`).then(r => r.json())
      .then(d => {
        if (typeof d.wander_enabled === 'boolean') setWanderEnabled(d.wander_enabled);
        if (typeof d.listen_music_playback === 'boolean') setListenMusicPlayback(d.listen_music_playback);
        if (d.listen_music_playback_device === 'mobile' || d.listen_music_playback_device === 'computer') {
          setListenMusicPlaybackDevice(d.listen_music_playback_device);
        }
      })
      .catch(() => {});
    fetch(`${getApiBase()}/api/sentinel/status`).then(r => r.json())
      .then(d => { if (typeof d.enabled === 'boolean') setSentinelEnabled(d.enabled); })
      .catch(() => {});
  }, []);

  const toggleWander = async () => {
    const next = !wanderEnabled;
    setWanderEnabled(next);
    try {
      // 持久化 + 即时生效
      await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'wander_enabled', value: next }),
      });
      await fetch(`${getApiBase()}/api/wander/${next ? 'start' : 'stop'}`, { method: 'POST' });
      fetchWanderStatus();
      toast('success', next ? '✅ 漫想模式已开启' : '漫想模式已关闭');
    } catch { setWanderEnabled(!next); toast('error', '❌ 设置失败'); }
  };

  const toggleSentinel = async () => {
    const next = !sentinelEnabled;
    setSentinelEnabled(next);
    try {
      // /api/sentinel/toggle 翻转并已自持久化到 settings.json
      const r = await fetch(`${getApiBase()}/api/sentinel/toggle`, { method: 'POST' });
      const d = await r.json();
      if (typeof d.enabled === 'boolean') setSentinelEnabled(d.enabled);
      toast('success', next ? '✅ 硅基感知已开启' : '硅基感知已关闭（AI 暂停感知你的健康/位置/状态）');
    } catch { setSentinelEnabled(!next); toast('error', '❌ 设置失败'); }
  };

  const toggleListenMusicPlayback = async () => {
    const next = !listenMusicPlayback;
    setListenMusicPlayback(next);
    try {
      const response = await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'listen_music_playback', value: next }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      toast('success', next ? '✅ 漫想听歌真实外放已开启' : '✅ 漫想听歌只做旋律/歌词分析');
    } catch {
      setListenMusicPlayback(!next);
      toast('error', '❌ 漫想听歌外放设置失败');
    }
  };

  const changeListenMusicPlaybackDevice = async (value: 'computer'|'mobile') => {
    const previous = listenMusicPlaybackDevice;
    setListenMusicPlaybackDevice(value);
    try {
      const response = await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'listen_music_playback_device', value }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      toast('success', `✅ 漫想听歌外放目标：${value === 'mobile' ? '手机' : '电脑'}`);
    } catch {
      setListenMusicPlaybackDevice(previous);
      toast('error', '❌ 漫想听歌外放目标设置失败');
    }
  };

  return (
    <>
      {/* 总开关 */}
      <div className="settings-section">
        <h4>主动性总开关</h4>
        <label className="toggle">
          <input type="checkbox" checked={wanderEnabled} onChange={toggleWander} />
          <span className="toggle-slider"></span>
          漫想模式
        </label>
        <p className="help-text">AI 在你空闲时主动发起联想、回忆、闲聊。关闭后不再主动漫想推送。</p>
        <label className="toggle" style={{ marginTop: '0.5rem' }}>
          <input type="checkbox" checked={sentinelEnabled} onChange={toggleSentinel} />
          <span className="toggle-slider"></span>
          硅基感知
        </label>
        <p className="help-text">AI 持续感知你的健康/位置/状态并在异常时主动关心。关闭后暂停所有采集、检测与推送。</p>
      </div>

      <div className="settings-section">
        <h4>🎵 漫想听歌外放</h4>
        <label className="toggle">
          <input type="checkbox" checked={listenMusicPlayback} onChange={toggleListenMusicPlayback} />
          <span className="toggle-slider"></span>
          真实播放（仪式感）
        </label>
        <p className="help-text">关闭时仍会分析旋律和歌词，不会调用播放器，也不会突然打断你当前的游戏或音乐。</p>
        <label className="help-text" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem' }}>
          外放目标
          <select
            value={listenMusicPlaybackDevice}
            disabled={!listenMusicPlayback}
            onChange={e => void changeListenMusicPlaybackDevice(e.target.value as 'computer'|'mobile')}
          >
            <option value="computer">电脑网易云</option>
            <option value="mobile">手机网易云</option>
          </select>
        </label>
        <p className="help-text">选择手机时必须有可用的 Mobile Relay；手机未连接会明确失败，不会偷偷改播电脑。</p>
      </div>

      {/* 刷手机模式 */}
      <div className="settings-section">
        <h4>📱 刷手机模式</h4>
        <div className="phone-browse-slider">
          <label>截图间隔：{phoneBrowseInterval}分钟</label>
          <input
            type="range" min="1" max="10" value={phoneBrowseInterval}
            onChange={e => {
              const v = parseInt(e.target.value);
              setPhoneBrowseInterval(v);
              fetch(`${getApiBase()}/api/settings`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'phone_browse_interval', value: v }),
              }).catch(() => {});
            }}
          />
          <span className="range-labels"><span>1</span><span>5</span><span>10</span></span>
        </div>
        <p className="help-text">手机后台时每隔此分钟数自动截图分析，AI 主动陪聊。</p>
        {getPlatform() === 'mobile' && phoneBrowseMode && (
          <p className="help-text" style={{ fontWeight: 'bold' }}>悬浮球状态：{widgetDiag}</p>
        )}
      </div>

      {/* 漫想手动测试 */}
      <div className="settings-section">
        <h4>漫想模式测试</h4>
        <div className="wander-test-controls">
          <button className="btn-secondary" onClick={async () => {
            try { await fetch(`${getApiBase()}/api/wander/start`, { method: 'POST' }); fetchWanderStatus(); }
            catch (e) { console.error('启动漫想模式失败:', e); }
          }}>手动开启漫想</button>
          <button className="btn-secondary" style={{ marginTop: '0.5rem' }} onClick={async () => {
            try { await fetch(`${getApiBase()}/api/wander/stop`, { method: 'POST' }); fetchWanderStatus(); }
            catch (e) { console.error('停止漫想模式失败:', e); }
          }}>手动关闭漫想</button>
        </div>
        {wanderStatus && (
          <div className="wander-debug-info" style={{ marginTop: '0.5rem', fontSize: '0.85em', color: '#888' }}>
            <div>漫想模式: {wanderStatus.mode === 'offline' ? '关闭' : wanderStatus.mode === 'idle' ? '发呆中' : '头脑风暴中'}</div>
            <div>创建循环: {wanderStatus.creator_running ? '运行中' : '已停止'}</div>
            <div>空闲: {Math.floor((wanderStatus.idle_seconds || 0) / 60)}分钟</div>
            <div>阈值: {Math.floor((wanderStatus.idle_threshold || 0) / 60)}分钟</div>
            <div>用户状态: {wanderStatus.user_status_display}{wanderStatus.user_status_custom_text ? `（${wanderStatus.user_status_custom_text}）` : ''}</div>
          </div>
        )}
      </div>
    </>
  );
};

export default SettingsInteractionTab;
