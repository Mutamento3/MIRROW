import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './CommerceSettings.css';

type LimitConfig = {
  per_order: number;
  daily_total: number;
  daily_orders: number | null;
};

type LimitDraft = {
  per_order: string;
  daily_total: string;
  daily_orders: string;
};

const DEFAULT_LIMITS: Record<string, LimitConfig> = {
  meituan: { per_order: 50, daily_total: 200, daily_orders: 5 },
};

const MERCHANT_LABELS: Record<string, string> = {
  meituan: '美团',
};

function draftsFromLimits(limits: Record<string, LimitConfig>): Record<string, LimitDraft> {
  return Object.fromEntries(Object.entries(limits).map(([merchant, config]) => [merchant, {
    per_order: String(config.per_order),
    daily_total: String(config.daily_total),
    daily_orders: config.daily_orders == null ? '' : String(config.daily_orders),
  }]));
}

/**
 * Shopping management belongs to Settings, not the chat composer. This
 * component owns the settings-only contact and limit forms; live confirmation
 * requests remain in CommerceActionRequests so neither component mirrors the
 * other's state or puts a fixed layer inside this panel.
 */
export default function CommerceSettings() {
  const [contactDraft, setContactDraft] = useState({ name: '', phone: '' });
  const [limits, setLimits] = useState<Record<string, LimitConfig>>(DEFAULT_LIMITS);
  const [limitDraft, setLimitDraft] = useState<Record<string, LimitDraft>>(draftsFromLimits(DEFAULT_LIMITS));
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);

  const loadLimits = useCallback(async () => {
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/limits`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      const incoming = data?.limits && typeof data.limits === 'object' ? data.limits : data;
      if (incoming && typeof incoming === 'object') {
        setLimits((current) => ({ ...current, ...incoming }));
        setLimitDraft(draftsFromLimits({ ...DEFAULT_LIMITS, ...incoming }));
      }
    } catch {
      // The settings page can still show safe defaults while the service is offline.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void loadLimits(); }, [loadLimits]);

  const updateContact = async () => {
    setBusy('contact-update');
    setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/contact/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...contactDraft, confirmed: true }),
      });
      if (!response.ok) throw new Error('联系人更新失败，系统凭据服务可能不可用');
      setContactDraft({ name: '', phone: '' });
    } catch (e) {
      setError(e instanceof Error ? e.message : '联系人更新失败');
    } finally {
      setBusy('');
    }
  };

  const clearContact = async () => {
    setBusy('contact-clear');
    setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/contact`, { method: 'DELETE' });
      if (!response.ok) throw new Error('联系人凭据清除失败');
      setContactDraft({ name: '', phone: '' });
    } catch (e) {
      setError(e instanceof Error ? e.message : '联系人凭据清除失败');
    } finally {
      setBusy('');
    }
  };

  const updateLimit = (merchant: string, field: keyof LimitDraft, value: string) => {
    setLimitDraft((current) => ({
      ...current,
      [merchant]: {
        ...(current[merchant] || { per_order: '', daily_total: '', daily_orders: '' }),
        [field]: value,
      },
    }));
  };

  const saveLimit = async (merchant: string) => {
    const draft = limitDraft[merchant];
    if (!draft) return;
    setBusy(`limit-${merchant}`);
    setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/commerce/limits/${merchant}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          per_order: Number(draft.per_order),
          daily_total: Number(draft.daily_total),
          daily_orders: draft.daily_orders.trim() === '' ? null : Number(draft.daily_orders),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '限额更新失败');
      if (data.limits) {
        setLimits((current) => ({ ...current, [merchant]: data.limits }));
      } else {
        setLimits((current) => ({
          ...current,
          [merchant]: {
            per_order: Number(draft.per_order),
            daily_total: Number(draft.daily_total),
            daily_orders: draft.daily_orders.trim() === '' ? null : Number(draft.daily_orders),
          },
        }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '限额更新失败');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="settings-section commerce-settings">
      <h4>🛒 购物</h4>
      <p className="help-text commerce-settings-intro">
        管理美团收货联系人和各平台购物限额。实际下单仍会在需要时单独请求人类伙伴确认。
      </p>

      <section className="commerce-settings-card" aria-labelledby="commerce-contact-heading">
        <div className="commerce-settings-card-title" id="commerce-contact-heading">美团联系人</div>
        <p className="help-text">尽量使用虚拟姓名；手机号只用于配送凭据，不会写入普通消息或通知。</p>
        <div className="commerce-settings-form">
          <label>
            <span>收货名</span>
            <input
              aria-label="购物收货名"
              autoComplete="name"
              placeholder="可用虚拟姓名"
              value={contactDraft.name}
              onChange={(e) => setContactDraft((current) => ({ ...current, name: e.target.value }))}
            />
          </label>
          <label>
            <span>手机号</span>
            <input
              aria-label="购物手机号"
              autoComplete="tel"
              inputMode="tel"
              placeholder="手机号"
              value={contactDraft.phone}
              onChange={(e) => setContactDraft((current) => ({ ...current, phone: e.target.value }))}
            />
          </label>
        </div>
        <div className="commerce-settings-actions">
          <button type="button" className="btn-primary" disabled={busy === 'contact-update'} onClick={updateContact}>保存联系人</button>
          <button type="button" className="btn-secondary" disabled={busy === 'contact-clear'} onClick={clearContact}>清除已缓存联系人</button>
        </div>
      </section>

      <section className="commerce-settings-card" aria-labelledby="commerce-limit-heading">
        <div className="commerce-settings-card-title" id="commerce-limit-heading">购物限额</div>
        <p className="help-text">单笔/日累计范围为 0–100000；日订单数留空表示不限，范围为 1–1000。</p>
        {!loaded && <div className="commerce-settings-loading">正在读取当前限额…</div>}
        <div className="commerce-limit-list">
          {Object.entries(limits).map(([merchant, config]) => {
            const draft = limitDraft[merchant] || {
              per_order: String(config.per_order),
              daily_total: String(config.daily_total),
              daily_orders: config.daily_orders == null ? '' : String(config.daily_orders),
            };
            return (
              <div className="commerce-limit-row" key={merchant}>
                <div className="commerce-limit-name">{MERCHANT_LABELS[merchant] || merchant}</div>
                <div className="commerce-limit-fields">
                  <label>
                    <span>单笔</span>
                    <input aria-label={`${merchant}单笔限额`} type="number" min="0.01" max="100000" step="0.01" value={draft.per_order} onChange={(e) => updateLimit(merchant, 'per_order', e.target.value)} />
                  </label>
                  <label>
                    <span>日累计</span>
                    <input aria-label={`${merchant}日累计限额`} type="number" min="0.01" max="100000" step="0.01" value={draft.daily_total} onChange={(e) => updateLimit(merchant, 'daily_total', e.target.value)} />
                  </label>
                  <label>
                    <span>日订单数</span>
                    <input aria-label={`${merchant}日订单数`} type="number" min="1" max="1000" step="1" value={draft.daily_orders} onChange={(e) => updateLimit(merchant, 'daily_orders', e.target.value)} placeholder="不限" />
                  </label>
                </div>
                <button type="button" className="btn-secondary commerce-limit-save" disabled={busy === `limit-${merchant}`} onClick={() => saveLimit(merchant)}>保存{MERCHANT_LABELS[merchant] || merchant}</button>
              </div>
            );
          })}
        </div>
      </section>

      {error && <div className="commerce-settings-error" role="alert">{error}</div>}
    </div>
  );
}
