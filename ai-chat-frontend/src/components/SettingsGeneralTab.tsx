import React from 'react';
import ConversationCovenantSettings from './ConversationCovenantSettings';
import type { ConversationCovenantSettings as CovenantSettings } from '../hooks/useConversationCovenant';

/**
 * 常规设置页 — 开机自启 / 清除对话 / 导入导出。
 * 依赖的 state 和操作函数由 App.tsx 通过 props 传入（避免重复 fetch 逻辑）。
 */
interface Props {
  autoStart: { mirrow: boolean };
  autoStartLoaded: boolean;
  toggleAutoStart: (component: 'mirrow', enable: boolean) => void;
  autoPlayVoice: boolean;
  toggleAutoPlayVoice: () => void;
  clearChat: () => void;
  clearOldMessages: () => void;
  covenantSettings: CovenantSettings;
  saveCovenantSettings: (settings: CovenantSettings) => Promise<boolean>;
  longTextCardFullContext: boolean;
  onLongTextCardFullContextChange: (value: boolean) => void;
}

export const SettingsGeneralTab: React.FC<Props> = ({ autoStart, autoStartLoaded, toggleAutoStart, autoPlayVoice, toggleAutoPlayVoice, clearChat, clearOldMessages, covenantSettings, saveCovenantSettings, longTextCardFullContext, onLongTextCardFullContextChange }) => {
  return (
    <>

      {/* 开机自启 */}
      <div className="settings-section">
        <h4>开机自启</h4>
        {autoStartLoaded && (
          <>
            <label className="toggle">
              <input type="checkbox" checked={autoStart.mirrow} onChange={() => toggleAutoStart('mirrow', !autoStart.mirrow)} />
              <span className="toggle-slider"></span>
              MIRROW 后端+前端
            </label>
            <p className="help-text">开机自动启动 start-all.bat（后端 + 前端）。</p>
          </>
        )}
      </div>

      {/* 语音播报 */}
      <div className="settings-section">
        <h4>语音播报</h4>
        <label className="toggle">
          <input type="checkbox" checked={autoPlayVoice} onChange={toggleAutoPlayVoice} />
          <span className="toggle-slider"></span>
          收到 AI 的消息时自动语音朗读
        </label>
        <p className="help-text">使用 AI 的声线（Edge TTS + RVC 音色转换）。首次合成需等待几秒，之后缓存秒播。</p>
      </div>

      <ConversationCovenantSettings
        key={`${covenantSettings.word}\u0000${covenantSettings.start_time}\u0000${covenantSettings.end_time}\u0000${covenantSettings.duration_minutes}`}
        settings={covenantSettings}
        onSave={saveCovenantSettings}
      />
      <div className="settings-section"><h4>长文小卡片</h4><label className="toggle"><input type="checkbox" checked={longTextCardFullContext} onChange={e => onLongTextCardFullContextChange(e.target.checked)} /><span className="toggle-slider"></span>后续轮次继续注入长文卡片原文</label><p className="help-text">首次发送始终注入全文；关闭后，AI 在后续轮次只会看到你填写的标题。</p></div>

      {/* 操作 */}
      <div className="settings-section">
        <h4>操作</h4>
        <button className="btn-secondary" onClick={clearChat}>
          清空对话
        </button>
        <button className="btn-secondary" onClick={clearOldMessages} style={{ marginTop: '0.5rem' }}>
          清理对话记录（保留最近7天）
        </button>
      </div>
    </>
  );
};

export default SettingsGeneralTab;
