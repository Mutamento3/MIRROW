export type Data = Record<string, unknown>;
export type MonitorTab = 'nodes' | 'api' | 'context' | 'neurons' | 'sentinel' | 'affect' | 'resources';
export type Tone = 'error' | 'warning' | 'unknown' | 'running' | 'ok' | 'idle';
export type SourceKey = 'system' | 'tasks' | 'neurons' | 'mobile' | 'sentinel' | 'ambient';
export type Source = { data: Data | null; updatedAt: number | null; error: string | null };
export type Sources = Record<SourceKey, Source>;
export type Fact = { label: string; value: string };
export type MonitorNode = { id: string; name: string; domain: string; tone: Tone; status: string; description: string; source: SourceKey; facts: Fact[]; destination?: MonitorTab };
export const record = (value: unknown): Data => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Data : {};
export const list = (value: unknown): Data[] => Array.isArray(value) ? value.map(record) : [];
// Defense in depth for compact UI summaries. The private backend remains the
// security boundary; never export arbitrary payloads or context from this view.
export function safeText(value: unknown): string {
  if (value == null || value === '') return '未采集';
  if (typeof value === 'object') return '见专项详情';
  return String(value).replace(/(?:https?:\/\/|wss?:\/\/)[^\s<>"']+/gi, '[地址已隐藏]')
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b/g, '[地址已隐藏]')
    .replace(/\bBearer\s+\S+|\bsk-[\w-]+/gi, '[凭据已隐藏]')
    .replace(/((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;}]+/gi, '$1[已隐藏]');
}
export const facts = (data: Data, fields: [string, string][]): Fact[] => fields.map(([key, label]) => ({ label, value: safeText(data[key]) }));
export const timeText = (value: unknown): string => {
  if (!value) return '尚无记录';
  // Backend's timezone-less timestamps are Beijing wall time, not device time.
  const text = String(value);
  const date = new Date(typeof value === 'number' ? value : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(text) ? `${text}+08:00` : text);
  return Number.isNaN(date.getTime()) ? safeText(value) : date.toLocaleString('zh-CN', { hour12: false });
};
const boolState = (value: unknown, inactive = '不可用'): Pick<MonitorNode, 'tone' | 'status'> => value === true ? { tone: 'ok', status: '可用' } : value === false ? { tone: inactive === '已停止' ? 'idle' : 'error', status: inactive } : { tone: 'unknown', status: '未确认' };
const execution = (value: unknown): Pick<MonitorNode, 'tone' | 'status'> => {
  switch (value) {
    case 'ok': case 'completed': return { tone: 'ok', status: '最近执行成功' };
    case 'error': case 'fail': case 'failed': return { tone: 'error', status: '执行失败' };
    case 'running': return { tone: 'running', status: '执行中' };
    case 'pending': return { tone: 'idle', status: '等待执行' };
    case 'skip': case 'skipped': return { tone: 'idle', status: '已跳过' };
    default: return { tone: 'unknown', status: value ? safeText(value) : '尚无执行记录' };
  }
};
export function buildNodes(sources: Sources): MonitorNode[] {
  const s = sources.system.data || {};
  const services = record(s.external_services), components = record(s.components_health);
  const nodes: MonitorNode[] = [];
  const add = (id: string, name: string, domain: string, description: string, state: Pick<MonitorNode, 'tone'|'status'>, source: SourceKey, detail: Fact[] = [], destination?: MonitorTab) => nodes.push({ id, name, domain, description, ...state, source, facts: detail, destination });
  add('backend', '后端服务', '基础与记忆', '主服务最近一次监控请求的结果。', s.backend_status === 'healthy' ? {tone:'ok', status:'请求成功'} : {tone:'unknown', status:'未确认'}, 'system');
  const memoryStatus = record(s.memory_health).status;
  add('memory', 'Memory V2', '基础与记忆', '当前记忆读模型就绪状态；旧资料引擎已退役。', memoryStatus === 'ready' || memoryStatus === 'recalled' ? {tone:'ok', status:'已就绪'} : memoryStatus === 'warming' ? {tone:'unknown', status:'预热中'} : memoryStatus === 'error' ? {tone:'error', status:'读取失败'} : {tone:'unknown', status:'未确认'}, 'system', facts(record(s.memory_health), [['status','状态'],['candidate_count','候选数'],['semantic_status','语义检索']]), 'resources');
  add('behavior', '行为调度器', '对话与认知', '模型回复与工具行动的调度入口；可用不等于某次调用已成功。', record(services.behavior_scheduler_observation).status === 'error' ? {tone:'unknown',status:'观测失败'} : boolState(services.behavior_scheduler_available), 'system', [], 'neurons');
  add('context', '上下文构建', '对话与认知', '查看本轮原料组织、实际调用快照与独立配方预览。', boolState(services.context_scheduler_available), 'system', [{label:'最近流水线',value:timeText(record(s.context_pipeline).last_run)}], 'context');
  const steps = record(record(s.context_pipeline).steps);
  add('faiss','FAISS 索引能力','基础与记忆','向量检索依赖可用性，索引统计见资源页。',boolState(services.faiss_available),'system',[],'resources');
  add('embedding','文本向量模型','基础与记忆','Sentence Transformer 依赖可用性，不代表某次检索成功。',s.sentence_transformer_status === 'available' ? {tone:'ok',status:'依赖可用'} : s.sentence_transformer_status === 'not_available' ? {tone:'error',status:'依赖不可用'} : {tone:'unknown',status:'未确认'},'system',[],'resources');
  const affect = record(components.affect);
  add('affect','情绪系统','对话与认知','情绪账本初始化与通道配置。',affect.error ? {tone:'unknown',status:'采集失败'} : boolState(affect.initialized),'system',facts(affect,[['engine_mode','引擎模式'],['self_enabled','自我通道'],['recognition_enabled','共振识别'],['channel_enabled','共振通道']]),'affect');
  for (const [id, raw] of Object.entries(steps)) {
    const step = record(raw);
    add(`step:${id}`, safeText(id), '上下文阶段', '最近一次流水线阶段记录，不代表正在执行。', execution(step.status), 'system', facts(step,[['duration_ms','耗时 ms'],['summary','结果摘要'],['error','错误']]), 'context');
  }
  for (const [key, name] of [['wander_running','漫想'],['sentinel_running','哨兵'],['music_monitor_running','音乐监听']]) {
    add(key,name,'感知与行动','后台组件的运行开关观测；停止不自动判定为故障。',boolState(components[key],'已停止'),'system',[],key === 'sentinel_running' ? 'sentinel' : 'neurons');
  }
  const scheduler = record(s.scheduler_status);
  add('scheduler','定时调度','后台维护','任务调度器与待执行计划。',boolState(scheduler.running,'已停止'),'system',facts(scheduler,[['jobs_count','计划任务数'],['next_mid_term_update','下次中期更新']]));
  const diary = record(s.diary_health);
  add('diary','日记连续性','后台维护','比较最新日记与最近活跃日期。',diary.error ? {tone:'unknown',status:'采集失败'} : diary.status === 'gap_detected' ? {tone:'warning',status:'发现日记间隙'} : diary.status === 'ok' ? {tone:'ok',status:'检查通过'} : {tone:'unknown',status:'未确认'},'system',facts(diary,[['last_entry_date','最新日记'],['last_active_date','最近活跃日'],['gap_days','间隔天数']]),'resources');
  const taskNames: Record<string,string> = { memory_consolidation:'记忆整合',memory_v2_online_ingest:'Memory V2 日总结',diary_generation:'日记生成',diary_backfill:'日记补全',unified_analysis:'认知整理',social_feed_summary:'共域收尾' };
  for (const task of [...list(sources.tasks.data?.tasks), ...list(sources.tasks.data?.background_tasks)]) {
    const id = String(task.name || 'unknown');
    add(`task:${id}`,task.label ? safeText(task.label) : taskNames[id] || safeText(id),'后台维护','后端实时任务状态；只有真实终态才显示结束。',task.stalled === true ? {tone:'warning',status:'任务卡住'} : execution(task.status),'tasks',facts(task,[['status','后端状态'],['progress','进度'],['message','最新进展']]));
  }
  for (const [id, raw] of Object.entries(record(s.cron_health))) {
    const cron = record(raw);
    add(`cron:${id}`,id === 'daily_maintenance' ? '每日维护' : id === 'task_check' ? '定时任务检查' : safeText(id),'后台维护','成功时间与留存错误分别展示；历史错误不等同当前失败。',cron.last_error ? {tone:'warning',status:'留有错误记录'} : cron.last_success ? {tone:'ok',status:'有成功记录'} : {tone:'idle',status:'尚无执行记录'},'system',[{label:'最近成功',value:timeText(cron.last_success)},{label:'留存错误',value:safeText(cron.last_error)}]);
  }
  const mobile = sources.mobile.data || {};
  const sentinel = sources.sentinel.data || {};
  const sourceStates = record(sentinel.source_states), sourceContracts = record(sentinel.source_contracts), sourceFreshness = record(sentinel.source_freshness);
  for (const name of new Set([...Object.keys(sourceContracts), ...Object.keys(sourceStates)])) {
    const state = sourceStates[name], freshness = record(sourceFreshness[name]), contract = record(sourceContracts[name]);
    const sourceState = state === 'connected' ? '已连接' : state === 'failed' ? '采集失败' : state === 'disabled' ? '已停用' : state === 'disconnected' ? '已断开' : '连接未报告';
    const factState = freshness.state === 'fresh' ? '事实新鲜' : freshness.state === 'stale' ? '事实已过期' : freshness.state === 'persistent' ? '持续声明' : '无有效时间';
    const tone: Tone = state === 'failed' || state === 'disconnected' ? 'error' : freshness.state === 'stale' ? 'warning' : freshness.state === 'fresh' || freshness.state === 'persistent' ? 'ok' : state === 'disabled' ? 'idle' : 'unknown';
    add(`sensor:${name}`,safeText(name),'感知数据源','连接健康和最近确认事实的时效是两件事。',{tone,status:`${sourceState} · ${factState}`} ,'sentinel',[
      {label:'最近确认',value:timeText(freshness.captured_at)},
      {label:'能说明',value:safeText(contract.proves)},
      {label:'不能单独说明',value:safeText(contract.does_not_prove)},
    ], 'sentinel');
  }
  add('mobile','手机连接','感知与行动','原生 Relay 与 WebView 的连接观测。',mobile.state === 'online' ? {tone:'ok',status:'在线'} : mobile.state === 'offline' ? {tone:'error',status:'离线'} : mobile.state === 'degraded' ? {tone:'warning',status:'陈旧 / 降级'} : {tone:'unknown',status:'未确认'},'mobile',facts(mobile,[['native_count','原生连接数'],['webview_count','WebView 连接数']]),'sentinel');
  for (const neuron of list(sources.neurons.data?.neurons)) {
    const trace = record(neuron.last_trace);
    add(`neuron:${neuron.neuron_id}`,safeText(neuron.name),'神经元',safeText(neuron.description || neuron.trigger_desc),execution(trace.status),'neurons',[
      ...facts(neuron,[['execution_count','执行次数'],['success_count','成功次数'],['recipe','配方'],['consumer','消费者']]),
      {label:'最近成功',value:timeText(neuron.last_ok_at)}, {label:'最近执行',value:timeText(trace.timestamp)},
      ...facts(trace,[['trace_id','执行记录 ID'],['duration_ms','耗时 ms'],['error_message','错误']]),
    ],'neurons');
  }
  const priority: Record<Tone,number> = {error:0,warning:1,unknown:2,running:3,ok:4,idle:5};
  return nodes.map(node => sources[node.source].error ? {...node,tone:'unknown' as const,status:'观测失败 · 保留旧记录'} : node)
    .sort((a,b) => priority[a.tone]-priority[b.tone] || a.name.localeCompare(b.name,'zh-CN'));
}
