import type { SentinelStatus } from '../../hooks/useSentinel';
import type { MobileLiveness } from '../../hooks/useMobileLiveness';
import { safeText, timeText, type Sources } from './monitorModel';
import AmbientListeningRecords from './AmbientListeningRecords';

const SOURCE_LABELS: Record<string,string> = {
  heart_rate:'心率', screen_activity:'电脑画面', input_idle:'键鼠输入',
  mirrow_visibility:'MIRROW 可见性', user_status:'状态声明', period:'生理期',
  step_count:'步数', wifi_state:'家庭 WiFi', gps_location:'外出位置',
  screen_time:'手机屏幕时长', current_app:'手机前台应用', mobile_vision:'手机视觉',
  notifications:'手机通知', running_processes:'电脑进程', remote_session:'远程会话',
  sensor_context:'手机传感器', weather:'天气',
};
const BASELINE_LABELS: Record<string,string> = {
  reply_interval:'回复间隔', heart_rate:'心率', steps:'步数', sleep:'睡眠',
  outing:'外出', pc_active:'电脑活跃', push_feedback:'主动联系反馈',
};
const DELIVERY_LABELS: Record<string,string> = {
  not_requested:'Flash 判断无需联系', pending:'准备生成', deferred:'对话中，延迟投递',
  pushed:'已生成并推送', cancelled_after_reply:'延迟期间收到回复，已取消',
  cancelled_by_delivery_gate:'当前发送准入受抑制或未确认，已取消',
  generation_empty:'生成结果为空', generation_failed:'生成失败',
  delivery_unavailable:'投递链路不可用',
};

function connectionText(value: unknown) {
  return ({connected:'已连接',failed:'失败',disconnected:'已断开',disabled:'已停用',unavailable:'不可用'} as Record<string,string>)[String(value)] || '未报告';
}
function freshnessText(value: unknown) {
  return ({fresh:'新鲜',stale:'已过有效期',persistent:'持续声明',unknown:'没有有效时间'} as Record<string,string>)[String(value)] || '未确认';
}
function ageText(value: number | null | undefined) {
  if (value == null) return '尚无确认记录';
  if (value < 90) return `${Math.round(value)} 秒前`;
  if (value < 5400) return `${Math.round(value / 60)} 分钟前`;
  return `${(value / 3600).toFixed(value < 36000 ? 1 : 0)} 小时前`;
}
function ttlText(value: number | null | undefined) {
  if (value == null) return '持续到下一次变更';
  if (value < 3600) return `${Math.round(value / 60)} 分钟`;
  return `${value / 3600} 小时`;
}
function idleText(value: number | null | undefined) {
  if (value == null) return '未确认';
  return value < 120 ? `${Math.round(value)} 秒` : `${Math.round(value / 60)} 分钟`;
}

export default function MonitorPerception({sources}:{sources:Sources}) {
  const sentinel = sources.sentinel.data as unknown as SentinelStatus|null;
  const mobile = sources.mobile.data as unknown as MobileLiveness|null;
  const snapshot = sentinel?.last_snapshot;
  const observations = snapshot?.semantic_observations || [];
  const contracts = sentinel?.source_contracts || {};
  const freshness = sentinel?.source_freshness || {};
  const baseline = sentinel?.baseline_diagnostics;
  const flash = sentinel?.debug?.last_flash_context;

  return <div className="monitor-resource-grid">
    <AmbientListeningRecords source={sources.ambient}/>

    <section className="monitor-resource-card"><h3>手机连接</h3>
      {sources.mobile.error && <p className="monitor-inline-error">观测失败，保留上次记录。</p>}
      <p className="monitor-description">采集于 {timeText(sources.mobile.updatedAt)}</p>
      {mobile ? <><p>{({online:'在线',offline:'离线',degraded:'陈旧 / 降级'} as Record<string,string>)[mobile.state] || '未确认'}</p>
        <dl className="monitor-facts"><div><dt>原生 Relay</dt><dd>{mobile.native_count ?? '未采集'} 个连接</dd></div><div><dt>WebView</dt><dd>{mobile.webview_count ?? '未采集'} 个连接</dd></div>
        <div><dt>通知待投递</dt><dd>{mobile.delivery_outbox?.pending ?? '未采集'}</dd></div><div><dt>通知发送中</dt><dd>{mobile.delivery_outbox?.sending ?? '未采集'}</dd></div></dl>
        {mobile.connections?.map((connection,index)=><p className="monitor-description" key={index}>{safeText(connection.client_kind)} · 最近确认 {connection.heartbeat_age_seconds == null ? '未确认' : Math.round(connection.heartbeat_age_seconds)+' 秒前'}</p>)}
        {mobile.last_disconnect_reason && <p className="monitor-description">上次断开：{safeText(mobile.last_disconnect_reason)}</p>}
      </> : <p className="monitor-blank">尚无手机观测</p>}
    </section>

    <section className="monitor-resource-card"><h3>当前感知快照</h3>
      {sources.sentinel.error && <p className="monitor-inline-error">观测失败，保留上次记录。</p>}
      <p className="monitor-description">{sentinel?.running ? '哨兵运行中' : '哨兵已停止'} · Tick {sentinel?.tick_count ?? '未采集'} · 快照 {timeText(snapshot?.observed_at)}</p>
      {snapshot ? <dl className="monitor-facts">
        <div><dt>行为状态</dt><dd>{safeText(snapshot.behavior_state)}</dd></div>
        <div><dt>位置与环境</dt><dd>{safeText(snapshot.location_label || snapshot.location_address)}<small> · 登记地点范围匹配</small></dd></div>
        <div><dt>人类伙伴声明</dt><dd>{safeText(snapshot.user_status)}<small>{snapshot.user_status_since ? ` · 自 ${timeText(snapshot.user_status_since)}` : ''}</small></dd></div>
        <div><dt>手机前台</dt><dd>{snapshot.mobile_screen_on === false ? '已熄屏' : safeText(snapshot.mobile_app_name || snapshot.mobile_app_package)}<small>{snapshot.mobile_foreground_resolution ? ` · ${safeText(snapshot.mobile_foreground_resolution)}` : ''}</small></dd></div>
        <div><dt>电脑前台</dt><dd>{safeText(snapshot.foreground_app)}</dd></div>
        <div><dt>本地输入空闲</dt><dd>{idleText(snapshot.physical_input_idle_seconds ?? snapshot.input_idle_seconds)}</dd></div>
        <div><dt>远程输入空闲</dt><dd>{idleText(snapshot.remote_input_idle_seconds)}</dd></div>
        <div><dt>手环 / 步数</dt><dd>{snapshot.heart_rate == null ? '心率未确认' : `${snapshot.heart_rate} 次/分`} · {snapshot.steps_today == null ? '步数未确认' : `${snapshot.steps_today} 步`}</dd></div>
        <div><dt>手机屏幕时长</dt><dd>{snapshot.screen_time_minutes == null ? '未确认' : `${snapshot.screen_time_minutes} 分钟`}<small>{snapshot.screen_time_logical_date ? ` · 逻辑日 ${safeText(snapshot.screen_time_logical_date)}` : ''}</small></dd></div>
      </dl> : <p className="monitor-blank">尚无感知快照</p>}
    </section>

    <section className="monitor-resource-card monitor-resource-wide"><h3>数据源：连接与事实时效</h3>
      <p className="monitor-description">“已连接”只表示通道可用；“新鲜”才表示最近确认的事实仍在各自有效窗口内。</p>
      {Object.keys(contracts).length ? <dl className="monitor-facts monitor-source-facts">{Object.entries(contracts).map(([name,contract])=>{
        const item = freshness[name];
        return <div key={name}><dt>{SOURCE_LABELS[name] || safeText(name)}</dt><dd>
          <span className={`monitor-state-chip ${item?.state || 'unknown'}`}>{connectionText(sentinel?.source_states?.[name])} · {freshnessText(item?.state)}</span>
          <small>最近确认 {ageText(item?.age_seconds)} · 参考有效期 {ttlText(item?.max_age_seconds ?? contract.nominal_max_age_seconds)}</small>
          {sentinel?.source_errors?.[name] && <p className="monitor-inline-error">{safeText(sentinel.source_errors[name])}</p>}
          <details><summary>这个来源能说明什么</summary><p className="monitor-description">能说明：{safeText(contract.proves)}<br/>不能单独说明：{safeText(contract.does_not_prove)}</p></details>
        </dd></div>;
      })}</dl> : <p className="monitor-blank">尚无来源合同</p>}
    </section>

    <section className="monitor-resource-card"><h3>跨源事实</h3>
      <p className="monitor-description">这里只并列事实、来源差异与时效，不替 AI 下结论。</p>
      {observations.length ? observations.map((item,index)=><article className="monitor-observation" key={`${item.key || 'observation'}-${index}`}>
        <div><span>{safeText(item.state)}</span><small>{item.attention_eligible ? '可唤醒 Flash' : '仅作上下文'} · {safeText(item.confidence)}</small></div>
        <p>{safeText(item.text)}</p>
        <small>观测于 {timeText(item.observed_at)} · 来源 {safeText((item.evidence || []).map(e=>String(e.source || '未知')).join('、'))}</small>
      </article>) : <p className="monitor-blank">当前没有需要额外对照的跨源事实</p>}
    </section>

    <section className="monitor-resource-card"><h3>个人基线</h3>
      <p className="monitor-description">总体置信度 {baseline?.confidence == null ? '未确认' : `${Math.round(baseline.confidence * 100)}%`}。样本不足只表示暂不适合比较，不表示状态正常。</p>
      <dl className="monitor-facts">{Object.entries(baseline?.dimensions || {}).map(([name,item])=><div key={name}><dt>{BASELINE_LABELS[name] || safeText(name)}</dt><dd>{item.available ? safeText(item.summary) : '尚未形成'}<small> · {item.sample_days ?? 0} 个有效日</small></dd></div>)}</dl>
    </section>

    <section className="monitor-resource-card"><h3>最近一次 Flash 判断</h3>
      {flash ? <><p className="monitor-description">{safeText(flash.summary)}</p><dl className="monitor-facts">
        <div><dt>发生时间</dt><dd>{timeText(flash.timestamp)}</dd></div>
        <div><dt>触发原因</dt><dd>{safeText(flash.triggers?.join('、'))}</dd></div>
        <div><dt>联系意愿</dt><dd>{flash.should_push ? '想主动联系' : '无需主动联系'}</dd></div>
        <div><dt>投递结果</dt><dd>{DELIVERY_LABELS[flash.delivery_state || ''] || safeText(flash.delivery_state)}</dd></div>
        <div><dt>判断理由</dt><dd>{safeText(flash.push_motivation)}</dd></div>
      </dl><details><summary>查看当次基线与接地信息</summary><p className="monitor-description">{safeText(flash.baseline_context)}</p>{flash.grounding_rejections?.length ? <p className="monitor-description">接地修正：{safeText(flash.grounding_rejections.join('；'))}</p> : null}</details></> : <p className="monitor-blank">本次进程尚无 Flash 判断</p>}
    </section>

    <section className="monitor-resource-card"><h3>最近哨兵记录</h3>
      {sentinel?.recent_alerts?.length ? <dl className="monitor-facts">{sentinel.recent_alerts.slice(0,12).map((alert,index)=><div key={alert.id ?? index}><dt>{safeText(alert.priority)}<br/>{timeText(alert.timestamp)}</dt><dd>{safeText(alert.message || alert.rule_id)}</dd></div>)}</dl> : <p className="monitor-description">{sentinel?'当前记录中暂无告警或判断':'尚未取得哨兵记录'}</p>}
    </section>
  </div>;
}
