import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { MainChatModelProfile } from '../hooks/useMainChatModel';
import './MainChatSourceDialog.css';

export function mainChatSourceLabel(profile: MainChatModelProfile) {
  return [profile.source_label, profile.billing_label].filter(Boolean).join(' · ') || profile.label;
}

interface Props {
  label: string;
  profiles: MainChatModelProfile[];
  activeId: string;
  switching: boolean;
  onConfirm: (profile: MainChatModelProfile) => Promise<void>;
  onClose: () => void;
}

export default function MainChatSourceDialog({ label, profiles, activeId, switching, onConfirm, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const radioName = useId();
  const [candidate, setCandidate] = useState(profiles.find(profile => profile.id === activeId)?.id || '');
  const [error, setError] = useState('');
  const selected = profiles.find(profile => profile.id === candidate);

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);

  const confirm = async () => {
    if (!selected?.available || switching) return;
    setError('');
    try {
      await onConfirm(selected);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '切换失败，请重试');
    }
  };

  return createPortal(
    <dialog ref={dialog} className="main-source-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); if (!switching) onClose(); }}
      onClick={event => { if (event.target === event.currentTarget && !switching) onClose(); }}>
      <div className="main-source-dialog-content">
        <header>
          <h3 id={titleId}>{label} · 选择线路</h3>
          <button type="button" aria-label="关闭线路选择" disabled={switching} onClick={onClose}>×</button>
        </header>
        <p>选择供应商与计费方式，确认后从下一次聊天生效。</p>
        <fieldset disabled={switching}>
          <legend>供应商线路</legend>
          {profiles.map(profile => (
            <label key={profile.id} className={`main-source-option${candidate === profile.id ? ' selected' : ''}`}>
              <input type="radio" name={radioName} value={profile.id} checked={candidate === profile.id}
                disabled={!profile.available} onChange={() => setCandidate(profile.id)} />
              <span>
                <strong>{mainChatSourceLabel(profile)}{profile.id === activeId && <small>当前使用</small>}</strong>
                <span className="main-source-model">{profile.model}</span>
                {!profile.available && <span>未配置：{profile.missing_env.join('、')}</span>}
              </span>
            </label>
          ))}
        </fieldset>
        <p className="main-source-note">55 按量和按次均复用已有 55 Key。按次以 API 请求计数，工具续轮也会单独计费；不同线路的思考过程以实际返回为准。</p>
        {error && <p className="main-source-error" role="alert">{error}</p>}
        <footer>
          <button type="button" disabled={switching} onClick={onClose}>取消</button>
          <button type="button" className="main-source-confirm" disabled={!selected?.available || switching}
            onClick={() => void confirm()}>{switching ? '正在切换…' : '确认使用此线路'}</button>
        </footer>
      </div>
    </dialog>, document.body,
  );
}
