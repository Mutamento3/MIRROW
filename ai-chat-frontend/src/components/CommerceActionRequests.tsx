import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './CommerceActionRequests.css';

type ActionRequest = {
  request_id: string;
  kind: string;
  status: string;
  fields: string[];
  expires_at?: string | null;
  recognized_rows?: string[];
  warning?: string;
};

type Props = {
  /** Polling can be disabled by a host page that does not render chat UI. */
  enabled?: boolean;
};

/**
 * Only renders live commerce requests that need 人类伙伴's decision.
 *
 * Contact management and spending limits live in CommerceSettings, which is
 * mounted inside Settings. Keeping this layer request-only means an idle chat
 * has no floating commerce controls competing with the message composer.
 */
export default function CommerceActionRequests({ enabled = true }: Props) {
  const [actions, setActions] = useState<ActionRequest[]>([]);
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});
  const [busy, setBusy] = useState<string>('');
  const [error, setError] = useState<string>('');

  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/action-requests`, { cache: 'no-store' });
      if (response.ok) {
        const data = await response.json();
        setActions(Array.isArray(data) ? data : []);
      }
    } catch {
      // Chat remains usable when the optional action poll is unavailable.
    }
  }, [enabled]);

  useEffect(() => {
    load();
    if (!enabled) return undefined;
    const interval = window.setInterval(load, 5000);
    return () => window.clearInterval(interval);
  }, [enabled, load]);

  const update = (id: string, field: string, value: string) => {
    setValues((current) => ({ ...current, [id]: { ...(current[id] || {}), [field]: value } }));
  };

  const resolve = async (action: ActionRequest, confirmed: boolean) => {
    setBusy(action.request_id);
    setError('');
    try {
      const data = values[action.request_id] || {};
      const response = await fetch(`${getApiBase()}/api/commerce/action-requests/${encodeURIComponent(action.request_id)}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed, ...data }),
      });
      if (!response.ok) throw new Error('请求处理失败');
      setValues((current) => {
        const next = { ...current };
        delete next[action.request_id];
        return next;
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : '请求处理失败');
    } finally {
      setBusy('');
    }
  };

  // This action remains available from a live contact-confirmation request;
  // it is not exposed as a standalone control in the chat idle state.
  const clearContact = async () => {
    setBusy('contact-clear');
    setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/contact`, { method: 'DELETE' });
      if (!response.ok) throw new Error('联系人凭据清除失败');
    } catch (e) {
      setError(e instanceof Error ? e.message : '联系人凭据清除失败');
    } finally {
      setBusy('');
    }
  };

  if (!enabled || actions.length === 0) return null;

  return (
    <aside className="commerce-action-requests" role="alert" aria-live="polite">
      <div className="commerce-request-title">需要人类伙伴确认的购物信息</div>
      {actions.map((action) => {
        const data = values[action.request_id] || {};
        const isContact = action.kind === 'meituan_contact_confirmation';
        return (
          <section className="commerce-request" key={action.request_id}>
            {isContact ? (
              <>
                <div className="commerce-request-copy">首次读取美团地址联系人。建议使用虚拟姓名，尽量不要使用真实姓名；手机号被遮挡时请手动填写，系统不会猜测。</div>
                {!!action.warning && <div className="commerce-request-warning">{action.warning}</div>}
                {!!action.recognized_rows?.length && (
                  <div className="commerce-recognized-rows" aria-label="识别出的脱敏地址行">
                    <div className="commerce-recognized-title">识别到的脱敏地址行</div>
                    {action.recognized_rows.map((row) => <div key={row}>{row}</div>)}
                  </div>
                )}
                <input
                  aria-label="收货名"
                  placeholder="收货名（可用虚拟姓名）"
                  value={data.name || ''}
                  onChange={(e) => update(action.request_id, 'name', e.target.value)}
                />
                <input
                  aria-label="手机号"
                  placeholder="手机号"
                  value={data.phone || ''}
                  onChange={(e) => update(action.request_id, 'phone', e.target.value)}
                />
              </>
            ) : (
              <>
                <div className="commerce-request-copy">GPS 不在已保存的家/公司范围，请确认配送地点。</div>
                <input
                  aria-label="配送地点"
                  placeholder="地点/小区"
                  value={data.place || ''}
                  onChange={(e) => update(action.request_id, 'place', e.target.value)}
                />
                <input
                  aria-label="门牌详情"
                  placeholder="门牌详情"
                  value={data.detail || ''}
                  onChange={(e) => update(action.request_id, 'detail', e.target.value)}
                />
              </>
            )}
            <div className="commerce-request-actions">
              <button type="button" disabled={busy === action.request_id} onClick={() => resolve(action, true)}>确认</button>
              <button type="button" disabled={busy === action.request_id} onClick={() => resolve(action, false)}>取消</button>
              {isContact && <button type="button" disabled={busy === 'contact-clear'} onClick={clearContact}>清除已缓存联系人</button>}
            </div>
            <div className="commerce-request-id">请求 ID：{action.request_id}</div>
          </section>
        );
      })}
      {error && <div className="commerce-request-error" role="status">{error}</div>}
    </aside>
  );
}
