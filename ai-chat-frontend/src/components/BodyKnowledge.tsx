import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import './BodyKnowledge.css';

export interface BodyKnowledgeData {
  revision: string;
  systems: { name: string; description: string; source: string }[];
  records: { domain: string; capability: string; details: string; limit: string }[];
  event_names: string[];
  narrative: { stale: boolean; message?: string; content?: string; archived_content?: string; updated_at?: string };
}

export function BodyKnowledgeContent({ data }: { data: BodyKnowledgeData }) {
  const narrative = data.narrative;
  const prose = narrative.archived_content || narrative.content;
  return <section className="body-knowledge" aria-label="我的身体">
    <p className="body-knowledge-note">身体说明来自现役模块，是只读资料，不是自我书的主观认识。接入能力、设备当前可用与实际执行成功是不同事实；调用方式由当前通道的工具说明提供。</p>
    {data.systems.map(item => <section key={item.name}><h3>{item.name}</h3><p>{item.description}</p></section>)}
    <h3>能力与边界</h3>
    {data.records.map(item => <details key={`${item.domain}:${item.capability}`}>
      <summary>{item.domain} · {item.capability}</summary>
      <p>{item.details}</p><p className="body-knowledge-note">{item.limit}</p>
    </details>)}
    <section><h3>现役自主活动</h3><p>{data.event_names.length ? data.event_names.join('、') : '本次未取得活动目录。'}</p></section>
    {prose && <details className="body-knowledge-archive">
      <summary>{narrative.stale ? '旧版架构散文 · 已过期' : '架构散文 · 叙述视图'}</summary>
      <p className="body-knowledge-note">{narrative.message}</p>
      <div className="body-knowledge-prose">{prose}</div>
    </details>}
  </section>;
}

export default function BodyKnowledge() {
  const [data, setData] = useState<BodyKnowledgeData | null>(null);
  const [error, setError] = useState('');
  const [request, setRequest] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(''); setData(null);
    fetch(`${getApiBase()}/api/wander/body-knowledge`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('身体说明读取失败');
        return response.json() as Promise<BodyKnowledgeData>;
      }).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(() => { if (!controller.signal.aborted) setError('身体说明暂时无法读取，未使用旧散文代替当前事实。'); });
    return () => controller.abort();
  }, [request]);
  return <div className="body-knowledge-container">
    <div className="body-knowledge-toolbar"><span>我的身体 · 只读</span><button onClick={() => setRequest(value => value + 1)}>重新读取</button></div>
    {error ? <p role="alert">{error}</p> : data ? <BodyKnowledgeContent data={data} /> : <p role="status">正在读取身体说明…</p>}
  </div>;
}
