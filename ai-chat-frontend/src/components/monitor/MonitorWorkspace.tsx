import { useMemo, useState } from 'react';
import { buildNodes, timeText, type Sources, type MonitorNode, type MonitorTab } from './monitorModel';

const sourceNames: Record<keyof Sources,string> = {system:'系统观测',tasks:'后台任务',neurons:'神经元记录',mobile:'手机连接',sentinel:'哨兵观测',ambient:'环境聆听'};
export default function MonitorWorkspace({sources,onNavigate}:{sources:Sources;onNavigate:(tab:MonitorTab)=>void}) {
  const [query,setQuery] = useState('');
  const [domain,setDomain] = useState('全部领域');
  const [filter,setFilter] = useState('all');
  const [selected,setSelected] = useState<string|null>(null);
  const [frozen,setFrozen] = useState<{node:MonitorNode;at:number|null}|null>(null);
  const nodes = useMemo(() => buildNodes(sources),[sources]);
  const domains = [...new Set(nodes.map(node => node.domain))];
  const filtered = nodes.filter(node => (domain === '全部领域' || node.domain === domain) &&
    (filter === 'all' || (filter === 'problems' ? ['error','warning','unknown'].includes(node.tone) : node.tone === 'running')) &&
    `${node.name} ${node.description} ${node.id}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const current = frozen?.node || nodes.find(node => node.id === selected);
  const source = current ? sources[current.source] : null;
  const at = frozen ? frozen.at : source?.updatedAt;
  const counts = { problems:nodes.filter(node => ['error','warning'].includes(node.tone)).length, running:nodes.filter(node => node.tone === 'running').length, unknown:nodes.filter(node => node.tone === 'unknown').length };
  const open = (node:MonitorNode) => {setSelected(node.id);setFrozen(null);};
  return <>
    <section className="monitor-pulse" aria-label="节点观测摘要">
      <button onClick={() => setFilter('problems')}><span>需要留意</span><strong className="monitor-count-warning">{counts.problems}</strong><small>失败或待核查记录</small></button>
      <button onClick={() => setFilter('running')}><span>执行中的任务</span><strong>{counts.running}</strong><small>仅统计已采集状态</small></button>
      <button onClick={() => setFilter('problems')}><span>尚未确认</span><strong>{counts.unknown}</strong><small>缺少记录或观测失败</small></button>
      <button onClick={() => {setFilter('all');setDomain('全部领域');setQuery('');}}><span>全部节点</span><strong>{nodes.length}</strong><small>查看完整观测列表</small></button>
    </section>
    <div className="monitor-source-strip">{Object.entries(sources).map(([key,value]) => <span key={key} className={value.error ? 'source-failed' : ''}>
      <i aria-hidden="true">{value.error ? '!' : value.updatedAt ? '·' : '○'}</i>{sourceNames[key as keyof Sources]} · {value.error ? '观测失败，保留旧记录' : value.updatedAt ? timeText(value.updatedAt) : '等待采集'}
    </span>)}</div>
    <div className={`monitor-workspace${current ? ' has-selection' : ''}`}>
      <section className="monitor-node-browser" aria-label="节点列表">
        <div className="monitor-node-tools">
          <input aria-label="搜索节点" placeholder="搜索节点、职责…" value={query} onChange={event => setQuery(event.target.value)} />
          <select aria-label="按领域筛选" value={domain} onChange={event => setDomain(event.target.value)}><option>全部领域</option>{domains.map(item => <option key={item}>{item}</option>)}</select>
          <div className="monitor-filters">{[['all','全部'],['problems','只看问题'],['running','执行中']].map(([key,label]) => <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div>
        </div>
        <div className="monitor-node-list"><div className="monitor-list-caption">{filtered.length} 个节点 · 问题优先</div>
          {filtered.map(node => <button className={`monitor-node-row${current?.id === node.id ? ' selected' : ''}`} key={node.id} onClick={() => open(node)} aria-pressed={current?.id === node.id}>
            <i className={`monitor-dot tone-${node.tone}`} aria-hidden="true" />
            <span><strong>{node.name}</strong><small>{node.domain}</small></span>
            <em className={`monitor-node-state tone-${node.tone}`}>{node.status}</em><span aria-hidden="true">›</span>
          </button>)}
          {!filtered.length && <p className="monitor-blank">没有符合筛选条件的节点。</p>}
        </div>
      </section>
      <section className="monitor-inspector" aria-label="节点检查器">
        {current ? <>
          <div className="monitor-inspector-toolbar"><button onClick={() => {setSelected(null);setFrozen(null);}}>← 节点列表</button><button aria-pressed={Boolean(frozen)} onClick={() => setFrozen(frozen ? null : {node:current,at:source?.updatedAt ?? null})}>{frozen ? '恢复跟随刷新' : '固定本次观测'}</button></div>
          <span className="monitor-eyebrow">{current.domain} / 节点详情</span><h2>{current.name}</h2>
          <span className={`monitor-state-badge tone-${current.tone}`}>{current.status}</span>
          <p className="monitor-description">{current.description}</p>
          <div className="monitor-observed">{frozen ? '已固定快照' : '当前观测'} · {timeText(at)}{source?.error && <p role="status">此来源最新采集失败；以下为上次记录，不能据此确认当前状态。</p>}</div>
          <dl className="monitor-facts">{current.facts.map((fact,index) => <div key={`${fact.label}-${index}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>
          {current.destination && <button className="monitor-open-detail" onClick={() => onNavigate(current.destination!)}>{current.destination === 'context' ? '查看实际上下文与配方' : current.destination === 'sentinel' ? '查看感知与手机诊断' : current.destination === 'resources' ? '查看资源与统计' : '查看神经元执行历史'} ↗</button>}
          <p className="monitor-inspector-note">节点间尚未统一关联一次执行；专项页保留各自真实记录。</p>
        </> : <div className="monitor-inspector-empty"><span aria-hidden="true">◎</span><h2>从一个节点开始</h2><p>选择左侧节点，查看当前观测、最近执行与诊断入口。</p><small>配置、可用性与执行结果分别呈现。</small></div>}
      </section>
    </div>
  </>;
}
