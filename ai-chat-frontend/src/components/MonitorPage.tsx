import { useState } from 'react';
import ContextRecipePanel, { type RecipeCall } from './ContextRecipePanel';
import CognitionRecipePreview from './CognitionRecipePreview';
import NeuronPanel from './NeuronPanel';
import AffectMonitorPanel from './AffectMonitorPanel';
import MonitorWorkspace from './monitor/MonitorWorkspace';
import MonitorResources from './monitor/MonitorResources';
import ApiCallsMonitor from './monitor/ApiCallsMonitor';
import MonitorPerception from './monitor/MonitorPerception';
import { useMonitorData } from './monitor/useMonitorData';
import { record, type MonitorTab } from './monitor/monitorModel';
import './monitor/MonitorPage.css';

const tabs: {key:MonitorTab;label:string;hint:string}[] = [
  {key:'nodes',label:'节点与异常',hint:'从异常出发，找到对应的运行记录。'},
  {key:'neurons',label:'执行记录',hint:'各神经元的真实调用、输入输出和历史。'},
  {key:'context',label:'上下文',hint:'实际调用快照与配方预览分别查看。'},
  {key:'sentinel',label:'感知与手机',hint:'哨兵数据源、最近告警与手机连接。'},
  {key:'affect',label:'情绪诊断',hint:'Affect 的只读高级诊断。'},
  {key:'resources',label:'资源与统计',hint:'记忆、维护和今日模型用量。'},
  {key:'api',label:'API 调用',hint:'按节点查看来源、失败原因与完整顺位经过。'},
];

export default function MonitorPage({onBack,onFetchBrainArchitecture}:{
  onBack:()=>void;onFetchBrainArchitecture:()=>void;
}) {
  const [tab,setTab] = useState<MonitorTab>('nodes');
  const [contextView,setContextView] = useState<'actual'|'preview'>('actual');
  const monitor = useMonitorData();
  const system = monitor.sources.system.data;
  return <div className="monitor-fullscreen monitor-v2">
    <div className="monitor-v2-shell">
      <header className="monitor-v2-header">
        <div className="monitor-v2-title"><button onClick={onBack} aria-label="返回聊天">‹</button><div><span className="monitor-eyebrow">MIRROW / OBSERVATORY</span><h1>监控台</h1></div></div>
        <div className="monitor-v2-controls">
          <label><span>自动刷新</span><select aria-label="自动刷新间隔" value={monitor.intervalSeconds} onChange={event=>monitor.setIntervalSeconds(Number(event.target.value))}><option value={0}>已暂停</option><option value={10}>10 秒</option><option value={30}>30 秒</option><option value={60}>60 秒</option></select></label>
          <button onClick={()=>void monitor.refresh()} disabled={monitor.loading}>{monitor.loading?'采集中…':'刷新'}</button>
          <button onClick={onFetchBrainArchitecture}>大脑架构 ↗</button>
        </div>
      </header>
      <nav className="monitor-v2-tabs" aria-label="监控分区">{tabs.map(item=><button key={item.key} onClick={()=>setTab(item.key)} aria-current={tab===item.key?'page':undefined}>{item.label}</button>)}</nav>
      <div className="monitor-v2-subtitle"><p>{tabs.find(item=>item.key===tab)?.hint}</p><span>本地诊断 · 刷新不触发业务动作</span></div>
      {monitor.sources.system.error && <div className="monitor-inline-error" role="status">系统观测未更新：{monitor.sources.system.error}。{system?'已保留上次快照。':'其他诊断分区仍可使用。'}</div>}
      {tab==='nodes' && <MonitorWorkspace sources={monitor.sources} onNavigate={setTab}/>}
      <div className="monitor-specialist" hidden={tab==='nodes'}>
        {tab==='neurons' && <NeuronPanel/>}
        {tab==='context' && <>
          <div className="monitor-filters"><button aria-pressed={contextView==='actual'} onClick={()=>setContextView('actual')}>实际调用记录</button><button aria-pressed={contextView==='preview'} onClick={()=>setContextView('preview')}>配方预览</button></div>
          {contextView==='preview' ? <CognitionRecipePreview /> : <><p className="monitor-description">只展示已记录调用；未发生的调用没有实际快照。</p><ContextRecipePanel recipeCallLog={record(system?.recipe_call_log) as unknown as Record<string,RecipeCall>} recipeCallLogObservation={record(system?.recipe_call_log_observation) as {status?:string}} /></>}
        </>}
        {tab==='sentinel' && <MonitorPerception sources={monitor.sources}/>}
        {tab==='affect' && <AffectMonitorPanel/>}
        {tab==='resources' && <MonitorResources data={system}/>}
        {tab==='api' && <ApiCallsMonitor intervalSeconds={monitor.intervalSeconds}/>}
      </div>
    </div>
  </div>;
}
