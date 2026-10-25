import { useState } from 'react';
import { sourceOptions, type ApiConfig } from './apiSourcesApi';
import { officialFlashSource, planGroupSwitch } from './groupSwitch';

export default function GroupSwitchPanel({ config, disabled, onChange }: { config: ApiConfig; disabled: boolean; onChange: (config: ApiConfig) => void }) {
  const [groups, setGroups] = useState<string[]>([]);
  const [source, setSource] = useState('');
  const [includeOverrides, setIncludeOverrides] = useState(false);
  const options = sourceOptions(config);
  let plan: ReturnType<typeof planGroupSwitch> | null = null;
  let error = '';
  if (groups.length && source) {
    try { plan = planGroupSwitch(config, groups, source, includeOverrides); }
    catch (cause) { error = cause instanceof Error ? cause.message : '无法生成切换方案'; }
  }
  const label = (id: string) => options.find(item => item.id === id)?.label ?? id;
  const flash = officialFlashSource(config);
  return <details className="api-batch-switch">
    <summary>批量切换任务组来源</summary>
    <p>选择多个任务组，将同一个模型放到首位，保留已有回退顺位。应用后点击页面上方“保存”生效。</p>
    <button type="button" disabled={disabled || !flash} onClick={() => { setGroups(config.groups.filter(group => group.prefer_official_flash).map(group => group.id)); setSource(flash); setIncludeOverrides(false); }}>准备人格与感知任务的官方 Flash 方案</button>
    <div className="api-batch-groups">{config.groups.map(group => <label className="api-check" key={group.id}><input type="checkbox" disabled={disabled} checked={groups.includes(group.id)} onChange={event => setGroups(event.target.checked ? [...groups, group.id] : groups.filter(id => id !== group.id))}/>{group.label}</label>)}</div>
    <label>统一首位模型<select disabled={disabled} value={source} onChange={event => setSource(event.target.value)}><option value="">选择站点与模型</option>{options.map(item => <option key={item.id} value={item.id} disabled={item.disabled}>{item.label} · {item.hint}</option>)}</select></label>
    <label className="api-check"><input type="checkbox" disabled={disabled} checked={includeOverrides} onChange={event => setIncludeOverrides(event.target.checked)}/>同时调整选中组的独立节点，保留各自回退顺位</label>
    {error && <p className="api-error" role="status">{error}</p>}
    {plan && <div className="api-switch-preview"><p>将调整 {plan.changedGroups} 个任务组顺位、{plan.rows.filter(row => row.changed).length} 个节点的有效来源；{plan.rows.filter(row => row.independent && !includeOverrides).length} 个独立节点保持原配置。</p><ul>{plan.rows.map(row => <li key={`${row.group}:${row.node}`}><strong>{row.node}</strong><small>原：{row.before.map(label).join(' → ')}<br/>新：{row.after.map(label).join(' → ')}{row.independent && !includeOverrides ? '（独立配置保留）' : !row.changed ? '（已是当前顺位）' : ''}</small></li>)}</ul></div>}
    <button type="button" disabled={disabled || !plan || (!plan.changedGroups && !plan.rows.some(row => row.changed))} onClick={() => { if (plan) { onChange(plan.next); setGroups([]); setSource(''); setIncludeOverrides(false); } }}>应用到待保存配置</button>
  </details>;
}
