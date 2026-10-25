import { useCallback, useEffect, useRef, useState } from 'react';
import { apiRequest, type CallsSnapshot } from '../api-sources/apiSourcesApi';
import { safeText, timeText } from './monitorModel';
import '../api-sources/ApiSources.css';

const legacyCategories: Record<string, string> = { structured: '历史 · 结构化后台任务', cognition: '历史 · 漫想认知任务', main: '主大脑', expression: '表达生成', vision: '视觉／OCR', test: '连接测试' };
const statuses: Record<string, string> = { ok: '成功', refusal: '明确拒答', timeout: '超时', cancelled: '已取消', http_error: '接口失败', connection_error: '连接失败', empty_content: '空结果', empty_choice: '空响应', json_parse_error: 'JSON 无效', validation_error: '校验失败', error: '失败', running: '调用中' };
const errors: Record<string, string> = { rate_limited: '站点限流', authentication_failed: '密钥或权限无效', request_format_error: '请求格式不被站点接受', model_not_found: '站点未找到模型', request_timeout: '等待超时', chain_timeout: '顺位等待超时', connection_failed: '无法连接站点', output_incomplete: '输出未完整结束', content_is_not_json_object: '未返回 JSON 对象', node_validation_failed: '节点字段合同未通过', model_refusal: '模型明确拒答', provider_refusal: '站点明确拒绝内容', invalid_observation: '缺少有效观察', invalid_plan_envelope: '计划字段无效', invalid_extension: '续延时间无效', invalid_move_reason: '缺少落子理由' };

export default function ApiCallsMonitor({ intervalSeconds = 30 }: { intervalSeconds?: number }) {
  const [data, setData] = useState<CallsSnapshot | null>(null);
  const [days, setDays] = useState(1);
  const [date, setDate] = useState(() => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date()));
  const [category, setCategory] = useState('');
  const [node, setNode] = useState('');
  const [station, setStation] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setLoading(true);
    const timer = window.setTimeout(() => current.abort(), 20000);
    try {
      const params = new URLSearchParams({ days: String(days), date, category, node, status, station_id: station, page: String(page) });
      const result = await apiRequest<CallsSnapshot>(`calls?${params}`, { signal: current.signal });
      if (!current.signal.aborted) { setData(result); setError(''); }
    } catch (cause) {
      if (controller.current === current) setError(current.signal.aborted ? '调用记录读取超时，可刷新重试' : cause instanceof Error ? cause.message : '读取失败');
    } finally { window.clearTimeout(timer); if (controller.current === current) setLoading(false); }
  }, [days, date, category, node, status, station, page]);
  useEffect(() => { void refresh(); return () => { controller.current?.abort(); }; }, [refresh]);
  useEffect(() => {
    if (!intervalSeconds) return;
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, intervalSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [intervalSeconds, refresh]);
  const change = (setter: (value: string) => void, value: string) => { setter(value); setPage(0); };
  const totals = (data?.summary ?? []).reduce((sum, row) => ({ calls: sum.calls + row.calls, success: sum.success + row.success, failures: sum.failures + row.failures, refusals: sum.refusals + row.refusals }), { calls: 0, success: 0, failures: 0, refusals: 0 });
  const nodeLabel = (id: string | undefined) => safeText(id ? data?.node_labels?.[id] ?? id : '未知节点');
  const categories = { ...legacyCategories, ...data?.category_labels };
  const summaryCategories = [...new Set(data?.summary.map(row => row.category) ?? [])];
  return <div className="api-calls">
    <div className="api-calls-toolbar"><label>按天查看 · 北京时间<input type="date" value={date} onChange={event=>change(setDate,event.target.value)}/></label><button type="button" onClick={()=>change(setDate,'')}>改用时间范围</button></div>
    <div className="api-calls-toolbar"><label>时间范围<select disabled={!!date} value={days} onChange={event => { setDays(Number(event.target.value)); setPage(0); }}><option value={1}>最近 24 小时</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option><option value={365}>最近 1 年</option></select></label><label>任务分类<select value={category} onChange={event => change(setCategory, event.target.value)}><option value="">全部分类</option>{Object.entries(categories).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label>节点<select value={node} onChange={event => change(setNode, event.target.value)}><option value="">全部节点</option>{(data?.nodes ?? []).map(id => <option key={id} value={id}>{nodeLabel(id)}</option>)}</select></label><label>API 站点<select value={station} onChange={event => change(setStation, event.target.value)}><option value="">全部站点</option>{(data?.stations ?? []).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>尝试结果<select value={status} onChange={event => change(setStatus, event.target.value)}><option value="">全部结果</option>{Object.entries(statuses).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button type="button" disabled={loading} onClick={() => void refresh()}>{loading ? '读取中…' : '刷新调用记录'}</button></div>
    {error && <p className="api-error" role="status">{error}{data ? '；保留上次快照。' : ''}</p>}
    <div className="api-call-metrics">{([['calls', '实际调用'], ['success', '成功'], ['failures', '失败'], ['refusals', '明确拒答']] as const).map(([key, label]) => <div key={key}><strong>{data ? totals[key] : '—'}</strong><span>{label}</span></div>)}</div>
    <p className="api-footnote">每次实际请求单独计数；缺少密钥或停用的来源不会计为调用。筛选匹配尝试，展开时保留该任务的完整顺位经过。{data && ` 快照：${timeText(data.updated_at)}`}</p>
    {summaryCategories.map(id => <details className="api-call-category" key={id} open><summary>{categories[id] ?? safeText(id)}</summary>{data?.summary.filter(row => row.category === id).map(row => <div className="api-call-summary" key={`${row.node}:${row.station_id}:${row.station_name}:${row.source_id}:${row.model}`}><div><strong>{nodeLabel(row.node)} · {safeText(row.station_name)}</strong><small>{safeText(row.model)}</small><small>{row.input_tokens == null ? '输入用量未提供' : `输入 ${row.input_tokens} Token`} · {row.output_tokens == null ? '输出用量未提供' : `输出 ${row.output_tokens} Token`}</small><small>{row.cached_input_tokens==null||!row.cache_reported_input_tokens?'缓存率未知（供应商未报告）':`已报告样本缓存率 ${(row.cached_input_tokens/row.cache_reported_input_tokens*100).toFixed(1)}% · 命中 ${row.cached_input_tokens} Token`}</small></div><div>调用 {row.calls} · 成功 {row.success} · 失败 {row.failures} · 拒答 {row.refusals}{row.running ? ` · 调用中 ${row.running}` : ''}</div></div>)}</details>)}
    <h4>逐次调用记录</h4>
    {data?.tasks.map(task => <details className="api-call-task" key={task.id}><summary><span>{nodeLabel(task.attempts[0]?.node)}<small>{timeText(task.attempts[0]?.started_at)} · {task.attempts.length} 次尝试</small></span><span className={`api-status ${task.status}`}>{statuses[task.status] ?? safeText(task.status)}</span></summary>{task.attempts.map(attempt => <article className="api-attempt" key={attempt.id}><div className="api-attempt-title"><strong>#{attempt.ordinal} {safeText(attempt.station_name)}</strong><span className={`api-status ${attempt.status}`}>{statuses[attempt.status] ?? safeText(attempt.status)}</span></div><dl><div><dt>模型</dt><dd>{safeText(attempt.model)}</dd></div><div><dt>开始时间</dt><dd>{timeText(attempt.started_at)}</dd></div><div><dt>结束时间</dt><dd>{attempt.ended_at ? timeText(attempt.ended_at) : '尚未记录结束'}</dd></div><div><dt>耗时</dt><dd>{attempt.duration_ms == null ? '—' : `${attempt.duration_ms} ms`}</dd></div>{attempt.error_code && <div><dt>失败原因</dt><dd>{errors[attempt.error_code] ?? safeText(attempt.error_code)}</dd></div>}{attempt.http_status > 0 && <div><dt>HTTP 状态</dt><dd>{attempt.http_status}</dd></div>}{attempt.trace_id && <div><dt>站点追踪 ID</dt><dd>{safeText(attempt.trace_id)}</dd></div>}<div><dt>用量</dt><dd>输入 {attempt.input_tokens ?? '未提供'} / 输出 {attempt.output_tokens ?? '未提供'}</dd></div></dl></article>)}</details>)}
    {data && !data.tasks.length && <p className="api-calls-empty">这个范围内没有已记录的调用。新版本启用前的用量日志不会伪装成逐次历史。</p>}
    <div className="api-pagination"><button type="button" disabled={page === 0 || loading} onClick={() => setPage(value => value - 1)}>上一页</button><span>{data ? `第 ${page + 1} 页 · ${data.total_tasks} 个任务` : '正在读取…'}</span><button type="button" disabled={!data || (page + 1) * data.page_size >= data.total_tasks || loading} onClick={() => setPage(value => value + 1)}>下一页</button></div>
  </div>;
}
