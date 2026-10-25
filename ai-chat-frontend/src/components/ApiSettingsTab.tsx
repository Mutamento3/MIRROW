import { useEffect, useRef, useState } from 'react';
import MainChatModelSettings from './MainChatModelSettings';
import { showToast } from './Toast';
import TaskPolicyTree from './api-sources/TaskPolicyTree';
import SourceHealth from './api-sources/SourceHealth';
import StationPanel from './api-sources/StationPanel';
import ApiCallsMonitor from './monitor/ApiCallsMonitor';
import RoutingCoverage from './api-sources/RoutingCoverage';
import { apiRequest, newId, profileDefaults, type ApiConfig, type ModelSource, type Station } from './api-sources/apiSourcesApi';
import './api-sources/ApiSources.css';

export default function ApiSettingsTab({sourcesOnly=false}:{sourcesOnly?:boolean}={}) {
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [page, setPage] = useState<'tasks'|'sources'|'calls'>(sourcesOnly?'sources':'tasks');
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [testing, setTesting] = useState('');
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [confirmReload, setConfirmReload] = useState(false);
  const inFlight = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    void apiRequest<ApiConfig>('config', { signal: controller.signal }).then(setConfig).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '读取失败'); });
    return () => controller.abort();
  }, []);

  const change = (next: ApiConfig) => { setConfig(next); setDirty(true); setError(''); setTestResults({}); };
  const updateStation = (id: string, changes: Partial<Station>) => {
    if (config) change({ ...config, stations: config.stations.map(station => station.id === id ? { ...station, ...changes } : station) });
  };
  const updateModel = (station: Station, id: string, changes: Partial<ModelSource>) => updateStation(station.id, { models: station.models.map(model => model.id === id ? { ...model, ...changes } : model) });
  const save = async () => {
    if (!config || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const body = { revision: config.revision, timeout_seconds: config.timeout_seconds, chains: config.chains, overrides: config.overrides,
        policies: config.policies ?? {}, assignments: config.assignments ?? {},
        stations: config.stations.map(({ key_configured: _configured, ...station }) => ({ ...station, ...(keys[station.id] ? { api_key: keys[station.id] } : {}) })) };
      const saved = await apiRequest<ApiConfig>('config', { method: 'PUT', body: JSON.stringify(body) });
      setConfig(saved); setKeys({}); setDirty(false); setTestResults({}); showToast('success', 'API 配置已保存，从下一次任务或聊天生效');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存失败'); }
    finally { setBusy(false); inFlight.current = false; }
  };
  const reload = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true);
    try { setConfig(await apiRequest<ApiConfig>('config')); setKeys({}); setDirty(false); setError(''); setTestResults({}); setConfirmReload(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '读取失败'); }
    finally { setBusy(false); inFlight.current = false; }
  };
  const test = async (id: string) => {
    if (inFlight.current) return;
    inFlight.current = true; setTesting(id);
    try {
      const result = await apiRequest<{ status: string; error_code: string; duration_ms: number; http_status: number }>(`test/${encodeURIComponent(id)}`, { method: 'POST', signal: AbortSignal.timeout(35000) });
      setTestResults(old => ({ ...old, [id]: result.status === 'ok' ? `连接与 JSON 校验通过 · ${result.duration_ms} ms` : `测试未通过 · ${result.error_code}${result.http_status ? ` · HTTP ${result.http_status}` : ''}` }));
    } catch (cause) { setTestResults(old => ({ ...old, [id]: cause instanceof Error ? cause.message : '测试失败' })); }
    finally { setTesting(''); inFlight.current = false; }
  };
  const modelInUse = (id: string) => config && [...Object.values(config.chains), ...Object.values(config.overrides), ...Object.values(config.policies??{}).flatMap(p=>[p.constant,p.peak,p.valley??[]])].some(chain => chain.includes(id));
  const addStation = () => {
    if (!config) return;
    const id = newId('relay');
    change({ ...config, stations: [...config.stations, { id, name: '新中转站', base_url: 'https://', protocol: 'openai-chat', key_env: `MIRROW_RELAY_KEY_${id.toUpperCase()}`, enabled: true, models: [] }] });
  };
  if (!config) return <div className="api-settings"><p role="status">{error || '正在读取 API 配置…'}</p><button type="button" onClick={() => void reload()} disabled={busy}>重新读取</button>{!sourcesOnly&&<section className="api-section"><MainChatModelSettings/></section>}</div>;
  return <div className="api-settings">
    <header className="api-settings-heading"><span className="api-eyebrow">MIRROW / CONNECTIONS</span><h3>API 配置</h3><p>把来源收好，让每个任务找到自己的顺位。</p></header>
    <div className="api-save-bar"><span>{dirty ? '有未保存的修改' : '与后端配置同步'}<small>新任务采用新策略 · 运行中保留快照</small></span><div><button type="button" disabled={busy || !!testing} onClick={() => dirty ? setConfirmReload(true) : void reload()}>重新载入 .env</button><button type="button" className="api-primary" disabled={!dirty || busy || !!testing} onClick={() => void save()}>{busy ? '保存中…' : '保存配置'}</button></div></div>
    {!sourcesOnly&&<div className="api-policy-tabs" role="tablist" aria-label="API设置页面">{([['tasks','任务策略'],['sources','模型来源'],['calls','线路记录']] as const).map(([id,label])=><button key={id} type="button" role="tab" aria-selected={page===id} onClick={()=>setPage(id)}>{label}</button>)}</div>}
    {confirmReload && <div className="api-notice" role="status">重新载入会放弃本页未保存的修改。<button type="button" disabled={busy} onClick={() => void reload()}>放弃并载入</button><button type="button" onClick={() => setConfirmReload(false)}>继续编辑</button></div>}
    {error && <p className="api-error" role="alert">{error}</p>}
    <section className="api-section" hidden={page!=='sources'}><SourceHealth revision={config.revision}/><div className="api-section-title"><div><h4>中转站列表</h4><p>先配置站点与密钥，再添加这个站点下的模型。</p></div><button type="button" disabled={busy || !!testing} onClick={addStation}>＋ 中转站</button></div>
      {config.stations.map(station => <StationPanel key={station.id} station={station} pendingKey={!!keys[station.id]}>
        <fieldset disabled={busy || !!testing}><div className="api-field-grid"><label>站点名称<input value={station.name} onChange={event => updateStation(station.id, { name: event.target.value })} maxLength={100}/></label><label>接口协议<select value={station.protocol} onChange={event => updateStation(station.id, { protocol: event.target.value })}><option value="openai-chat">OpenAI Chat 兼容</option><option value="openai-responses">OpenAI Responses 兼容</option><option value="gemini-native">Gemini 原生</option></select></label><label className="api-wide">接口地址<input type="url" value={station.base_url} placeholder="https://api.example.com/v1" onChange={event => updateStation(station.id, { base_url: event.target.value })}/></label><label className="api-wide">API Key<input type="password" autoComplete="new-password" value={keys[station.id] ?? ''} placeholder={station.key_configured ? '已配置；留空保留，填写则替换' : '填写该站点的密钥'} onChange={event => { setKeys(old => ({ ...old, [station.id]: event.target.value })); setDirty(true); }}/></label></div>
          <details className="api-advanced"><summary>后端配置与站点操作</summary><label>密钥环境变量<input value={station.key_env} onChange={event => updateStation(station.id, { key_env: event.target.value })}/></label><label className="api-check"><input type="checkbox" checked={station.enabled} onChange={event => updateStation(station.id, { enabled: event.target.checked })}/>启用这个站点</label><button type="button" disabled={station.models.some(model => modelInUse(model.id))} onClick={() => change({ ...config, stations: config.stations.filter(item => item.id !== station.id) })}>移除站点</button><small>顺位正在使用的站点需先从对应列表移除。</small></details>
          <div className="api-model-list">{station.models.map(model => <div className="api-model" key={model.id}><div className="api-model-header"><strong>{model.label}</strong><button type="button" disabled={dirty || !station.key_configured || !station.enabled || !model.enabled} onClick={() => void test(model.id)}>{testing === model.id ? '测试中…' : '生成测试（计费）'}</button></div><div className="api-field-grid"><label>显示名称<input value={model.label} onChange={event => updateModel(station, model.id, { label: event.target.value })}/></label><label>模型名<input value={model.model} placeholder="站点要求的完整 model 名" onChange={event => updateModel(station, model.id, { model: event.target.value })}/></label><label className="api-wide">模型适配<select value={model.profile} onChange={event => updateModel(station, model.id, { profile: event.target.value, ...profileDefaults(event.target.value) })}>{Object.entries(config.profiles).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label></div>
            <details className="api-advanced"><summary>高级适配与模型操作</summary><div className="api-field-grid"><label>结构化输出<select value={model.json_mode} onChange={event => updateModel(station, model.id, { json_mode: event.target.value })}><option value="prompt">提示词 JSON ＋本地校验</option><option value="json_object">原生 JSON Object ＋本地校验</option></select></label><label>思考模式<select disabled={station.protocol !== 'openai-chat' || !['qwen', 'siliconflow-qwen', 'siliconflow-deepseek', 'deepseek', 'glm'].includes(model.profile)} value={model.thinking} onChange={event => updateModel(station, model.id, { thinking: event.target.value })}><option value="default">按模型默认</option><option value="off">关闭</option><option value="on">开启</option></select><small>仅已支持的 Chat 适配发送开关；其他协议保留模型默认。</small></label><label>系统说明角色<select value={model.system_role} onChange={event => updateModel(station, model.id, { system_role: event.target.value })}><option value="system">system</option><option value="developer">developer</option><option value="user">并入 user，保留说明</option></select></label></div><label className="api-check"><input type="checkbox" checked={model.sampling} onChange={event => updateModel(station, model.id, { sampling: event.target.checked })}/>发送采样参数</label><label className="api-check"><input type="checkbox" checked={model.enabled} onChange={event => updateModel(station, model.id, { enabled: event.target.checked })}/>启用这个模型</label><button type="button" disabled={!!modelInUse(model.id)} onClick={() => updateStation(station.id, { models: station.models.filter(item => item.id !== model.id) })}>移除模型</button></details>
            {testResults[model.id] && <p className="api-test-result" role="status">{testResults[model.id]}</p>}
          </div>)}</div><button type="button" className="api-add" onClick={() => updateStation(station.id, { models: [...station.models, { id: newId('model'), label: '新模型', model: '', profile: 'generic', ...profileDefaults('generic'), enabled: true }] })}>＋ 添加模型</button>
        </fieldset></StationPanel>)}
      <p className="api-footnote">密钥只保存在后端 .env；页面仅显示是否配置。手动编辑后可重新载入。连接测试会发送一条独立测试请求，并计入调用记录。</p>
    </section>
    {!sourcesOnly&&<section className="api-section" hidden={page!=='tasks'}><MainChatModelSettings/></section>}
    {!sourcesOnly&&<section className="api-section" hidden={page!=='tasks'}><div className="api-section-title"><div><h4>任务供应策略</h4><p>大类统一顺位，必要时展开节点例外。主聊天的手动异模型选项优先于主大脑策略。</p></div></div>
      <p className="api-footnote">按上下文与合同家族分组。组内动态材料和具体合同仍可不同，实际缓存以服务返回为准。</p>
      <TaskPolicyTree config={config} disabled={busy || !!testing} onChange={change}/>
      <RoutingCoverage/>
      <label className="api-timeout">单个来源等待上限<select disabled={busy || !!testing} value={config.timeout_seconds} onChange={event => change({ ...config, timeout_seconds: Number(event.target.value) })}>{[30, 60, 90, 120, 180, 300, 600, ...(![30, 60, 90, 120, 180, 300, 600].includes(config.timeout_seconds) ? [config.timeout_seconds] : [])].map(value => <option key={value} value={value}>{value} 秒</option>)}</select></label><p className="api-footnote">正在运行的任务保留开始时的来源快照。整条链的等待预算按启用来源数计算；列表耗尽会如实报告失败。</p>
    </section>}
    {page==='calls'&&<section className="api-section"><p className="api-footnote">独立 API 诊断，不进入聊天、记忆或上下文。空返回与明确拒绝分别统计。</p>{page==='calls'&&<ApiCallsMonitor/>}</section>}
  </div>;
}
