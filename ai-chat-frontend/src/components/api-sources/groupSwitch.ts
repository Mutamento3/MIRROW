import type { ApiConfig } from './apiSourcesApi';

export type SwitchRow = { group: string; node: string; independent: boolean; changed: boolean; before: string[]; after: string[] };

export function planGroupSwitch(config: ApiConfig, groupIds: string[], sourceId: string, includeOverrides = false) {
  const station = config.stations.find(item => item.models.some(model => model.id === sourceId));
  const model = station?.models.find(item => item.id === sourceId);
  if (!station?.enabled || !station.key_configured || !model?.enabled) throw new Error('请选择已启用并配置密钥的模型来源');
  if (!groupIds.length || groupIds.some(id => !config.groups.some(group => group.id === id))) throw new Error('请选择有效任务组');
  const prepend = (chain: string[]) => {
    const next = [sourceId, ...chain.filter(id => id !== sourceId)];
    if (next.length > 32) throw new Error('顺位已达上限，请先移除一个回退来源');
    return next;
  };
  const next = { ...config, chains: { ...config.chains }, overrides: { ...config.overrides } };
  const rows: SwitchRow[] = [];
  let changedGroups = 0;
  for (const group of config.groups.filter(item => groupIds.includes(item.id))) {
    const inherited = config.chains[group.id];
    if (!inherited?.length) throw new Error('任务组缺少来源，请重新载入配置');
    next.chains[group.id] = prepend(inherited);
    if (inherited.join('\0') !== next.chains[group.id].join('\0')) changedGroups += 1;
    for (const node of group.nodes) {
      const independent = !!config.overrides[node.id];
      const before = config.overrides[node.id] ?? (node.single_source ? [] : inherited);
      const after = node.single_source || (independent && !includeOverrides) ? before : prepend(before);
      if (independent && includeOverrides && !node.single_source) next.overrides[node.id] = [...after];
      rows.push({ group: group.label, node: node.label, independent, changed: before.join('\0') !== after.join('\0'), before: [...before], after: [...after] });
    }
  }
  return { next, rows, changedGroups };
}

export function officialFlashSource(config: ApiConfig): string {
  return config.stations.filter(station => {
    try { return station.enabled && station.key_configured && station.protocol === 'openai-chat' && new URL(station.base_url).hostname === 'api.deepseek.com'; }
    catch { return false; }
  }).flatMap(station => station.models).find(model => model.enabled && model.profile === 'deepseek' && ['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4.1-flash', 'deepseek-v4-flash-vision-exp'].includes(model.model))?.id ?? '';
}
