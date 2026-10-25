import { useState, type ReactNode } from 'react';
import type { Station } from './apiSourcesApi';

export default function StationPanel({ station, pendingKey, children }: {
  station: Station; pendingKey: boolean; children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(station.models.length === 0);
  return <details className="api-station" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>
      <span className="api-station-mark" aria-hidden="true">⚙</span>
      <span><strong>{station.name}</strong><small>{station.models.length} 个模型 · {station.enabled ? '已启用' : '已停用'}</small></span>
      <span className="api-station-actions">
        <span className={`api-badge ${station.key_configured || pendingKey ? 'ready' : ''}`}>{pendingKey ? '待保存密钥' : station.key_configured ? '密钥已配置' : '缺少密钥'}</span>
        <button type="button" aria-label={`${expanded ? '收起' : '设置'} ${station.name}`} aria-expanded={expanded}
          onClick={event => { event.preventDefault(); setExpanded(value => !value); }}>{expanded ? '收起' : '设置'}</button>
      </span>
    </summary>
    {children}
  </details>;
}
