import { facts, record, safeText, type Data } from './monitorModel';

export default function MonitorResources({data}:{data:Data|null}) {
  if (!data) return <p className="monitor-blank">尚未取得系统统计。可以刷新重试，其他诊断页仍可打开。</p>;
  const sections: {key:string;name:string;fields:[string,string][]}[] = [
    {key:'token_stats',name:'今日模型用量',fields:[['today_calls','调用数'],['today_input_tokens','输入 Token'],['today_output_tokens','输出 Token'],['cache_hit_ratio','缓存命中比例'],['est_cost_yuan','估算成本 ¥']]},
    {key:'model_api_status',name:'模型配置',fields:[['deepseek','DeepSeek 已配置'],['glm','GLM 已配置'],['summary_model','摘要模型']]},
    {key:'vector_index_stats',name:'记忆检索',fields:[['source','记忆体系'],['status','就绪状态'],['event_count','有效事件'],['thread_count','事件链'],['group_count','交流原文窗']]},
    {key:'sessions_health',name:'会话',fields:[['active_sessions_count','活跃会话'],['active_topic_count','活跃话题']]},
    {key:'reminders_health',name:'提醒',fields:[['pending_count','待处理'],['last_injected_at','上次注入']]},
    {key:'keyword_pool_stats',name:'关键词池',fields:[['pool_size','词池大小'],['max_pool_size','上限'],['daily_additions_today','今日新增']]},
    {key:'migration_status',name:'记忆迁移',fields:[['last_migration_run','上次迁移'],['next_scheduled_migration','下次迁移']]},
  ];
  return <div className="monitor-resource-grid">{sections.map(section => <section className="monitor-resource-card" key={section.key}>
    <h3>{section.name}</h3>{Boolean(record(data[section.key]).error) && <p className="monitor-inline-error">此项采集失败</p>}
    <dl className="monitor-facts">{facts(record(data[section.key]),section.fields).map(fact => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>
    {section.key === 'model_api_status' && <small>已配置仅表示配置存在，不代表最近调用成功。</small>}
  </section>)}
    <section className="monitor-resource-card"><h3>旧资料档案分布（只读）</h3><dl className="monitor-facts">{Object.entries(record(record(data.bucket_stats).bucket_counts)).map(([key,value]) => <div key={key}><dt>{safeText(key)}</dt><dd>{safeText(value)}</dd></div>)}</dl></section>
    <section className="monitor-resource-card"><h3>调用方统计</h3><dl className="monitor-facts">{Object.entries(record(record(data.token_stats).caller_breakdown)).map(([key,value]) => <div key={key}><dt>{safeText(key)}</dt><dd>{typeof value === 'number' ? value : facts(record(value),[['calls','调用'],['input_tokens','输入'],['output_tokens','输出']]).map(item => `${item.label} ${item.value}`).join(' · ')}</dd></div>)}</dl><small>来自后端今日统计，不等同完整调用历史。</small></section>
  </div>;
}
