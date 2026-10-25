import React, { useState } from 'react';
import type { ConversationCovenantSettings as CovenantSettings } from '../hooks/useConversationCovenant';

interface Props {
  settings: CovenantSettings;
  onSave: (settings: CovenantSettings) => Promise<boolean>;
}

const ConversationCovenantSettings: React.FC<Props> = ({ settings, onSave }) => {
  const [draft, setDraft] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saved' | 'local'>('idle');

  const save = async () => {
    setSaving(true);
    const persistedToBackend = await onSave({ ...draft, word: draft.word.trim() });
    setDraft(current => ({ ...current, word: current.word.trim() }));
    setSaveState(persistedToBackend ? 'saved' : 'local');
    setSaving(false);
  };

  return (
    <div className="settings-section covenant-settings">
      <h4>自动结束聊天约定</h4>
      <div className="setting-row covenant-word-row">
        <label htmlFor="covenant-word">约定词</label>
        <input
          id="covenant-word"
          className="setting-input"
          type="text"
          value={draft.word}
          placeholder="留空则关闭"
          onChange={event => {
            setDraft({ ...draft, word: event.target.value });
            setSaveState('idle');
          }}
        />
      </div>
      <div className="covenant-time-range">
        <label>
          检测开始
          <input
            className="setting-input"
            type="time"
            value={draft.start_time}
            onChange={event => {
              setDraft({ ...draft, start_time: event.target.value });
              setSaveState('idle');
            }}
          />
        </label>
        <span>至</span>
        <label>
          检测结束
          <input
            className="setting-input"
            type="time"
            value={draft.end_time}
            onChange={event => {
              setDraft({ ...draft, end_time: event.target.value });
              setSaveState('idle');
            }}
          />
        </label>
      </div>
      <div className="setting-row covenant-duration-row">
        <label htmlFor="covenant-duration">倒计时（分钟）</label>
        <input
          id="covenant-duration"
          className="setting-input"
          type="number"
          min={1}
          max={1440}
          step={1}
          value={draft.duration_minutes}
          onChange={event => {
            setDraft({ ...draft, duration_minutes: Number(event.target.value) });
            setSaveState('idle');
          }}
        />
      </div>
      <p className="help-text">
        在时段内发送包含约定词的消息，会启动设定时长的倒计时；跨午夜时段同样有效。倒计时已运行时不会重复重置。
      </p>
      <div className="covenant-save-row">
        <button type="button" className="btn-primary" onClick={save} disabled={saving}>
          {saving ? '保存中…' : '保存约定'}
        </button>
        {saveState === 'saved' && <span className="covenant-save-status">已保存</span>}
        {saveState === 'local' && <span className="covenant-save-status warning">后端暂不可用，已保存在本机</span>}
      </div>
    </div>
  );
};

export default ConversationCovenantSettings;
