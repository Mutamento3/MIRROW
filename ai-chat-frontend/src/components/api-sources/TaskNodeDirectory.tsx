import SourceChain from './SourceChain';
import {sourceOptions} from './apiSourcesApi';
import type {ApiConfig, CategoryPolicy, TaskCategory} from './apiSourcesApi';

export default function TaskNodeDirectory({config, category, policy, stored, disabled, onChange}: {
  config: ApiConfig; category: TaskCategory; policy: CategoryPolicy; stored: boolean;
  disabled: boolean; onChange: (value: ApiConfig) => void;
}) {
  const familyOf = (node: TaskCategory['groups'][number]['nodes'][number], label: string) => node.function_label ?? label.split(' · ')[0];
  const functions = [...new Set(category.groups.flatMap(group => group.nodes.map(node => familyOf(node, group.label))))];
  return <div className="api-category-nodes">{functions.map(name => <details key={name}>
    <summary>{name}</summary>
    {category.groups.map(group => {
      const nodes = group.nodes.filter(node => familyOf(node, group.label) === name);
      if (!nodes.length) return null;
      return <details className="api-node-list" key={group.id}>
        <summary>{group.label} · {nodes.length} 节点</summary>
        <p className="api-footnote">{group.description}</p>
        {category.id !== 'main' && <label>缓存归类（本处节点）<select value={category.id} disabled={disabled} onChange={event => {
          const assignments = {...config.assignments};
          nodes.forEach(node => { assignments[node.id] = event.target.value; });
          onChange({...config, assignments});
        }}><option value="high">高缓存命中率</option><option value="low">低缓存命中率</option></select></label>}
        {nodes.map(node => <details className="api-node" key={node.id}>
          <summary><span>{node.label}</span><small>{node.single_source ? '独立单来源' : config.overrides[node.id] ? '独立顺位' : stored ? '继承大类' : '现有任务组顺位'}</small></summary>
          <p className="api-node-description">{node.description ?? group.description}</p>
          {node.single_source ? <label>复审来源（无自动回退）<select disabled={disabled} value={config.overrides[node.id]?.[0] ?? ''} onChange={event => {
            const overrides = {...config.overrides};
            if (event.target.value) overrides[node.id] = [event.target.value];
            else delete overrides[node.id];
            onChange({...config, overrides});
          }}><option value="">待配置，复审暂停</option>{sourceOptions(config).map(source => {
            const model = source.station.models.find(item => item.id === source.id)!;
            const roles = model.roles?.length ? model.roles : ['internal'];
            return <option key={source.id} value={source.id} disabled={source.disabled || !roles.includes('internal') || (!!model.allowed_nodes?.length && !model.allowed_nodes.includes(node.id))}>{source.label}</option>;
          })}</select></label> : <><label className="api-check"><input type="checkbox" disabled={disabled} checked={!!config.overrides[node.id]} onChange={event => {
            const overrides = {...config.overrides};
            if (event.target.checked) overrides[node.id] = [...(stored ? policy.constant : config.chains[group.id] ?? policy.constant)];
            else delete overrides[node.id];
            onChange({...config, overrides});
          }}/>独立常驻顺位</label>
          {config.overrides[node.id] && <SourceChain config={config} value={config.overrides[node.id]} stableId={category.id === 'main' ? 'direct_pro' : 'direct_flash'} disabled={disabled} onChange={value => onChange({...config, overrides:{...config.overrides, [node.id]:value}})}/>}</>}
        </details>)}
      </details>;
    })}
  </details>)}</div>;
}
