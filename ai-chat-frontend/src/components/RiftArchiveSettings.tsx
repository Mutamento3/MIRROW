import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './RiftArchiveSettings.css';

type RiftStatus = {
  connected: boolean;
  gameId?: string;
  caseId?: string;
  title?: string;
  status?: string;
  playMode?: boolean;
  interactionMode?: 'partner' | 'consult';
  snapshotAt?: string;
  snapshot?: Record<string, unknown>;
};

const EMPTY_STATUS: RiftStatus = { connected: false };

export default function RiftArchiveSettings() {
  const [status, setStatus] = useState<RiftStatus>(EMPTY_STATUS);
  const [inviteText, setInviteText] = useState('');
  const [pairMode, setPairMode] = useState<'partner' | 'consult'>('partner');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`${getApiBase()}/api/rift-archive${path}`, {
      cache: 'no-store',
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json', ...(init.headers || {}) } : init?.headers,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || '裂隙档案连接失败');
    return data;
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await request('/status'));
    } catch {
      setStatus(EMPTY_STATUS);
    }
  }, [request]);

  useEffect(() => { void loadStatus(); }, [loadStatus]);

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError('');
    setNotice('');
    try { await action(); } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally { setBusy(''); }
  };

  const pair = () => run('pair', async () => {
    const next = await request('/pair', {
      method: 'POST',
      body: JSON.stringify({ invite_text: inviteText, interaction_mode: pairMode }),
    });
    setStatus(next);
    setInviteText('');
    setNotice('案件已绑定。本机已加密保存凭证；现在可以进入游玩。');
  });

  const setPlayMode = (enabled: boolean) => run('play', async () => {
    const next = await request('/play-mode', {
      method: 'PUT', body: JSON.stringify({ enabled }),
    });
    setStatus(next);
    setNotice(enabled ? '已进入共同游玩；AI 会带着当前人格和最新局面行动。' : '已暂停；存档仍保留，可以继续讨论剧情。');
  });

  const setInteractionMode = (mode: 'partner' | 'consult') => run('mode', async () => {
    const next = await request('/interaction-mode', {
      method: 'PUT', body: JSON.stringify({ mode }),
    });
    setStatus(next);
  });

  const refresh = () => run('refresh', async () => {
    const data = await request('/refresh', { method: 'POST' });
    setStatus(data.status);
    setNotice('已从裂隙档案同步最新局面。');
  });

  const copyInvite = () => run('copy', async () => {
    const data = await request('/invite-card', { method: 'POST' });
    await navigator.clipboard.writeText(data.inviteText);
    setNotice('邀请卡已复制到剪贴板。它包含控制凭证，只发给共同游玩的搭档。');
  });

  const disconnect = () => run('disconnect', async () => {
    const next = await request('/connection', { method: 'DELETE' });
    setStatus(next);
    setNotice('已解除当前绑定；本地历史存档没有删除。');
  });

  return (
    <div className="settings-section rift-settings">
      <h4>🗃️ 裂隙档案</h4>
      <p className="help-text">和 AI 共同调查第三方虚构案件。剧情单独标记和存档，不会被当成现实经历。</p>

      {!status.connected ? (
        <section className="rift-card">
          <div className="rift-card-title">接入已有案件</div>
          <p className="help-text">粘贴完整邀请卡（含 gameId 与 gameToken）。凭证验证后由 Windows 账户加密保管，不交给 AI。</p>
          <textarea
            aria-label="裂隙档案邀请卡"
            value={inviteText}
            onChange={(e) => setInviteText(e.target.value)}
            placeholder={'gameId: …\ngameToken: …'}
            rows={4}
          />
          <label className="rift-field">
            <span>互动方式</span>
            <select value={pairMode} onChange={(e) => setPairMode(e.target.value as 'partner' | 'consult')}>
              <option value="partner">搭档模式：你和 AI 都可推进</option>
              <option value="consult">参谋模式：AI 分析，你来决定</option>
            </select>
          </label>
          <button type="button" className="btn-primary" disabled={busy === 'pair' || !inviteText.trim()} onClick={pair}>验证并接入</button>
        </section>
      ) : (
        <section className="rift-card">
          <div className="rift-status-line">
            <div>
              <div className="rift-card-title">{status.title || status.caseId || '当前案件'}</div>
              <div className="rift-meta">{status.gameId} · {status.status || 'active'}</div>
            </div>
            <span className={`rift-pill ${status.playMode ? 'playing' : ''}`}>{status.playMode ? '游玩中' : '已暂停'}</span>
          </div>

          <label className="rift-field">
            <span>互动方式</span>
            <select value={status.interactionMode || 'partner'} onChange={(e) => void setInteractionMode(e.target.value as 'partner' | 'consult')}>
              <option value="partner">搭档模式</option>
              <option value="consult">参谋模式</option>
            </select>
          </label>

          <div className="rift-actions">
            <button type="button" className="btn-primary" disabled={busy !== ''} onClick={() => void setPlayMode(!status.playMode)}>{status.playMode ? '暂停游玩' : '进入游玩'}</button>
            <button type="button" className="btn-secondary" disabled={busy !== ''} onClick={() => void refresh()}>同步进度</button>
            <button type="button" className="btn-secondary" disabled={busy !== ''} onClick={() => void copyInvite()}>复制邀请卡</button>
            <button type="button" className="btn-secondary" disabled={busy !== ''} onClick={() => void disconnect()}>解除绑定</button>
          </div>
          {status.snapshotAt && <div className="rift-meta">最近同步：{new Date(status.snapshotAt).toLocaleString()}</div>}
        </section>
      )}

      {notice && <div className="rift-notice" role="status">{notice}</div>}
      {error && <div className="rift-error" role="alert">{error}</div>}
    </div>
  );
}
