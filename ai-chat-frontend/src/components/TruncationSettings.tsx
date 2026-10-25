import React, { useState, useEffect, useCallback } from 'react';
import { getApiBase } from '../config';

// ── 类型定义 ──

interface TruncationConfig {
  key: string;
  default: number;
  recommended: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  group: string;
  display_name: string;
  description: string;
  current: number;
}

interface ConfirmDialog {
  type: 'unlimited' | 'below_recommended';
  config: TruncationConfig;
  value: number;
}

// ── 分组信息 ──

const GROUP_INFO: Record<string, { emoji: string; label: string; desc: string }> = {
  A: { emoji: '\u{1F9E0}', label: '记忆与日记', desc: '影响上下文一致性和记忆完整性。默认无限制以获得最佳效果。' },
  B: { emoji: '\u{1F4AC}', label: '上下文注入', desc: '发送给 LLM 的上下文截断参数。调低可节省 Token。' },
  C: { emoji: '\u{1F4E4}', label: '输出限制', desc: 'LLM 生成回复时的最大输出 Token 数。' },
  D: { emoji: '\u{1F4BE}', label: '存储上限', desc: '数据存储时的内容截断参数。' },
};

function unitLabel(unit: string): string {
  return unit === 'tokens' ? 'tokens' : '字符';
}

// ── 组件 ──

export default function TruncationSettings() {
  const [configs, setConfigs] = useState<TruncationConfig[]>([]);
  const [values, setValues] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialog | null>(null);

  // 加载配置
  useEffect(() => {
    fetch(`${getApiBase()}/api/settings`)
      .then(r => r.json())
      .then(data => {
        // 合并后端 settings 中的截断值 + truncation_config 元数据
        fetch(`${getApiBase()}/api/truncation-configs`)
          .then(r => r.json())
          .then(cfgs => {
            setConfigs(cfgs);
            const vals: Record<string, number> = {};
            for (const c of cfgs) {
              vals[c.key] = data[c.key] ?? c.default;
            }
            setValues(vals);
            setLoading(false);
          })
          .catch(() => {
            // 回退：只用 settings 数据
            const keys = [
              'topic_memory_cap', 'diary_msg_cap', 'diary_total_cap', 'yesterday_cap',
              'memory_inject_cap', 'ob_merge_cap', 'brain_doc_cap',
              'conv_msg_cap', 'flash_user_cap', 'flash_ai_cap', 'flash_reasoning_cap', 'flash_tools_cap',
              'persona_anchor_cap', 'persona_simplified_cap', 'query_enhance_cap', 'wander_query_cap',
              'worldbook_display_cap', 'diary_display_cap',
              'search_results_cap', 'parallel_outline_cap', 'parallel_summary_cap', 'fusion_cluster_cap',
              'phone_browse_tokens', 'sentinel_tokens',
              'bookmark_cap', 'ledger_ctx_cap', 'music_analyzer_cap',
            ];
            const vals: Record<string, number> = {};
            for (const k of keys) {
              vals[k] = data[k] ?? 0;
            }
            setValues(vals);
            setLoading(false);
          });
      })
      .catch(() => setLoading(false));
  }, []);

  // 更新单个值
  const setValue = useCallback((key: string, val: number) => {
    setValues(prev => (prev[key] === val ? prev : { ...prev, [key]: val }));
  }, []);

  // 确认按钮点击
  const handleApply = useCallback((config: TruncationConfig, value: number) => {
    if (value < config.min && value !== 0) return; // 禁用状态
    if (value === 0) {
      // 设为无限制 → 二次确认
      setConfirmDialog({ type: 'unlimited', config, value });
    } else if (value < config.recommended) {
      // 低于推荐值 → 二次确认
      setConfirmDialog({ type: 'below_recommended', config, value });
    } else {
      // 正常保存
      doApply(config.key, value, config.display_name);
    }
  }, []);

  // 执行保存
  const doApply = useCallback((key: string, value: number, displayName: string) => {
    fetch(`${getApiBase()}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    })
      .then(() => {
        setValues(prev => ({ ...prev, [key]: value }));
        setConfirmDialog(null);
      })
      .catch(err => {
        console.error(`保存截断设置 ${displayName} 失败:`, err);
        setConfirmDialog(null);
      });
  }, []);

  // 全部重置
  const handleResetAll = useCallback(() => {
    if (!confirm('确定要将所有截断设置重置为默认值吗？')) return;
    const promises = configs.map(c =>
      fetch(`${getApiBase()}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: c.key, value: c.default }),
      })
    );
    Promise.all(promises).then(() => {
      const vals: Record<string, number> = {};
      for (const c of configs) vals[c.key] = c.default;
      setValues(vals);
    }).catch(console.error);
  }, [configs]);

  if (loading) return <div className="settings-section"><p>加载截断设置中...</p></div>;
  if (!configs.length) return <div className="settings-section"><p>无法加载截断配置，请检查后端是否已更新。</p></div>;

  // 按分组归类
  const grouped: Record<string, TruncationConfig[]> = {};
  for (const c of configs) {
    const g = c.group || 'D';
    if (!grouped[g]) grouped[g] = [];
    grouped[g].push(c);
  }

  return (
    <div className="truncation-settings">
      <div className="truncation-header-row">
        <h4>📐 截断设置</h4>
        <button className="btn-secondary" onClick={handleResetAll} style={{ fontSize: '0.8rem' }}>
          全部重置为默认值
        </button>
      </div>

      {(['A', 'B', 'C', 'D'] as const).map(groupKey => {
        const items = grouped[groupKey];
        if (!items || items.length === 0) return null;
        const info = GROUP_INFO[groupKey];
        return (
          <div key={groupKey} className="truncation-group">
            <div className="truncation-group-header">
              <span className="truncation-group-emoji">{info.emoji}</span>
              <span className="truncation-group-label">{info.label}</span>
            </div>
            <p className="help-text" style={{ marginBottom: '0.5rem' }}>{info.desc}</p>
            {items.map(c => (
              <TruncationRow
                key={c.key}
                config={c}
                value={values[c.key] ?? c.default}
                onChange={v => setValue(c.key, v)}
                onApply={v => handleApply(c, v)}
              />
            ))}
          </div>
        );
      })}

      {/* 二次确认弹窗 */}
      {confirmDialog && (
        <div className="modal-overlay" onClick={() => setConfirmDialog(null)}>
          <div className="modal-content confirm-dialog" onClick={e => e.stopPropagation()}>
            {confirmDialog.type === 'unlimited' ? (
              <>
                <h4>⚠️ 关闭截断保护</h4>
                <p>
                  你正在关闭「{confirmDialog.config.display_name}」的截断。<br />
                  这可能导致单次 LLM 调用消耗大量 Token，甚至超时失败。
                </p>
                <p style={{ fontWeight: 600 }}>确定要设为无限制吗？</p>
                <div className="confirm-dialog-buttons">
                  <button className="btn-secondary" onClick={() => setConfirmDialog(null)}>取消</button>
                  <button className="btn-danger" onClick={() => doApply(confirmDialog.config.key, 0, confirmDialog.config.display_name)}>
                    确定无限制
                  </button>
                </div>
              </>
            ) : (
              <>
                <h4>⚠️ 低于推荐值</h4>
                <p>
                  当前值 <strong>{confirmDialog.value.toLocaleString()} {unitLabel(confirmDialog.config.unit)}</strong> 低于推荐值{' '}
                  <strong>{confirmDialog.config.recommended.toLocaleString()} {unitLabel(confirmDialog.config.unit)}</strong>，
                  可能影响「{confirmDialog.config.display_name}」的质量。
                </p>
                <p className="help-text">{confirmDialog.config.description}</p>
                <p style={{ fontWeight: 600 }}>确定要应用此设置吗？</p>
                <div className="confirm-dialog-buttons">
                  <button className="btn-secondary" onClick={() => setConfirmDialog(null)}>取消</button>
                  <button className="btn-primary" onClick={() => doApply(confirmDialog.config.key, confirmDialog.value, confirmDialog.config.display_name)}>
                    仍然应用
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── 单个设置行 ──

function TruncationRow({
  config,
  value,
  onChange,
  onApply,
}: {
  config: TruncationConfig;
  value: number;
  onChange: (v: number) => void;
  onApply: (v: number) => void;
}) {
  const [customInput, setCustomInput] = useState('');
  const isUnlimited = value === 0;
  const belowMin = value < config.min && value !== 0;
  const belowRec = value > 0 && value < config.recommended;
  const sliderVal = isUnlimited ? config.max : Math.min(value, config.max);

  const handleSlider = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseInt(e.target.value);
    onChange(v);
    setCustomInput('');
  };

  const handleCustomInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    setCustomInput(e.target.value);
  };

  const handleCustomBlur = () => {
    const parsed = parseInt(customInput);
    if (!isNaN(parsed) && parsed >= 0) {
      onChange(parsed);
    } else {
      setCustomInput('');
    }
  };

  const handleCustomKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      const parsed = parseInt(customInput);
      if (!isNaN(parsed) && parsed >= 0) {
        onChange(parsed);
        setCustomInput('');
      }
    }
  };

  return (
    <div className="truncation-row">
      <div className="truncation-row-header">
        <span className="truncation-label" title={config.description}>
          {config.display_name}
          {isUnlimited && <span className="unlimited-badge">无限制</span>}
          {belowRec && !isUnlimited && (
            <span className="warning-icon" title={`推荐 >=${config.recommended.toLocaleString()} ${unitLabel(config.unit)}。\n${config.description}`}>
              ⚠️
            </span>
          )}
        </span>
      </div>
      <div className="truncation-slider-row">
        <input
          type="range"
          min={config.min}
          max={config.max}
          step={config.step}
          value={sliderVal}
          onChange={handleSlider}
          className={isUnlimited ? 'slider-unlimited' : ''}
        />
        <span className={`truncation-value ${isUnlimited ? 'value-unlimited' : ''}`}>
          {isUnlimited ? '无限制' : `${value.toLocaleString()} ${unitLabel(config.unit)}`}
        </span>
        <input
          type="number"
          min={0}
          placeholder="0=无限制"
          value={customInput}
          onChange={handleCustomInput}
          onBlur={handleCustomBlur}
          onKeyDown={handleCustomKeyDown}
          className="setting-input inline-number"
          style={{ width: '80px' }}
        />
        <button
          disabled={belowMin}
          className={belowRec && !isUnlimited ? 'btn-warn' : 'btn-primary'}
          onClick={() => onApply(value)}
          title={belowMin ? `最低不能小于 ${config.min.toLocaleString()} ${unitLabel(config.unit)}` : ''}
          style={{ fontSize: '0.8rem', padding: '4px 10px' }}
        >
          {belowMin ? '🚫' : belowRec && !isUnlimited ? '⚠️' : '✅'} 应用
        </button>
      </div>
      <p className="help-text">
        {config.description}<br />
        推荐 &ge;{config.recommended.toLocaleString()} {unitLabel(config.unit)}。范围 {config.min.toLocaleString()} – {config.max.toLocaleString()} {unitLabel(config.unit)}。
      </p>
    </div>
  );
}
