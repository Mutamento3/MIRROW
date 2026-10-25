import { useState } from 'react';
import { sourceOptions, type ApiConfig } from './apiSourcesApi';

export default function SourceChain({ config, value, onChange, disabled = false, stableId }: { config: ApiConfig; value: string[]; onChange: (value: string[]) => void; disabled?: boolean; stableId?: string }) {
  const [adding, setAdding] = useState(false);
  const [stationId, setStationId] = useState('');
  const [sourceId, setSourceId] = useState('');
  const options = sourceOptions(config);
  const station = config.stations.find(item => item.id === stationId);
  const available = station?.models.filter(model => !value.includes(model.id) && model.id !== stableId) ?? [];
  const move = (index: number, direction: number) => {
    const next = [...value];
    [next[index], next[index + direction]] = [next[index + direction], next[index]];
    onChange(next);
  };
  const ordered = stableId ? [...value.filter(id=>id!==stableId),stableId] : value;
  return <div className="api-source-chain">
    <ol>{ordered.map((id, index) => {
      const item = options.find(option => option.id === id);
      return <li key={id}><span className="api-order">{index + 1}</span><div className="api-source-caption"><strong>{item?.label ?? '来源已移除'}</strong><small>{item?.hint ?? id}</small>{!!item?.allowedNodes?.length&&<small>仅适用于已登记的小结构节点，整理与记忆任务会跳过</small>}{item?.disabled && <small className="api-warning">当前停用或未配置密钥，运行时跳过</small>}</div>
        {id===stableId?<span className="api-badge ready">稳定回退</span>:<div className="api-row-actions"><button type="button" disabled={disabled || index === 0} onClick={() => {const next=[...ordered];[next[index],next[index-1]]=[next[index-1],next[index]];onChange(next);}} aria-label={`上移第 ${index + 1} 顺位`}>↑</button><button type="button" disabled={disabled || index >= ordered.length - (stableId?2:1)} onClick={() => stableId ? (()=>{const next=[...ordered];[next[index],next[index+1]]=[next[index+1],next[index]];onChange(next);})() : move(index, 1)} aria-label={`下移第 ${index + 1} 顺位`}>↓</button><button type="button" disabled={disabled || ordered.length <= 1} onClick={() => onChange(ordered.filter(key => key !== id))} aria-label={`移除 ${item?.label ?? id}`}>×</button></div>}
      </li>;
    })}</ol>
    {!adding ? <button type="button" className="api-add" disabled={disabled || value.length >= 32} onClick={() => { setAdding(true); setStationId(config.stations[0]?.id ?? ''); setSourceId(''); }}>＋ 新增 API 来源</button> : <div className="api-add-source">
      <label>中转站<select value={stationId} onChange={event => { setStationId(event.target.value); setSourceId(''); }}>{config.stations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>模型<select value={sourceId} onChange={event => setSourceId(event.target.value)}><option value="">选择模型</option>{available.map(model => <option key={model.id} value={model.id}>{model.label} · {model.model}</option>)}</select></label>
      <div className="api-row-actions"><button type="button" disabled={!sourceId || disabled} onClick={() => { onChange(stableId?[...ordered.filter(id=>id!==stableId),sourceId,stableId]:[...value,sourceId]); setAdding(false); }}>添加</button><button type="button" onClick={() => setAdding(false)}>取消</button></div>
      {!available.length && <small>该站点没有尚未加入的模型，请先在中转站列表添加。</small>}
    </div>}
  </div>;
}
