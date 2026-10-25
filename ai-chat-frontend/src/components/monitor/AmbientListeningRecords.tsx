import { list, record, safeText, timeText, type Data, type Source } from './monitorModel';

const OUTCOME_LABELS: Record<string,string> = {
  no_participation:'Flash 判断无需联系', participate_requested:'Flash 建议参与',
  event_committed:'观察事件已保存', message_generation_failed:'消息生成失败，事件已保留',
  message_cancelled_after_user_reply:'人类伙伴已回复，取消发送', message_sent:'消息已权威保存，客户端送达未单独确认',
  message_failed_event_retained:'消息提交失败，事件已保留',
  evaluation_failed_retry_pending:'判断链路失败，材料待重试',
  message_cancelled_stale_material:'材料已过时，取消发送',
};
const MATERIAL_LABELS: Record<string,string> = {
  received:'已收妥，等待本地分析', retry:'等待本地重试', failed:'本地分析失败',
  ready:'本地分析完成', voice_dictation_duplicate:'已确认是语音输入副本',
  stale_before_analysis:'已过时，不再转写', stale_before_judgment:'已过时，不参与推送',
  no_speech_detected:'短片段未检出人声，跳过转写（音频仍保留）',
};
const SOURCE_LABELS: Record<string,string> = {
  recent_local_pc_input:'近期本机键鼠操作，优先电脑',
  no_recent_local_pc_input:'本机键鼠记录已超时，使用手机',
  pc_physical_input_unconfirmed:'尚未确认本机键鼠输入，使用手机',
  pc_input_origin_unavailable:'本机输入检测不可用，使用手机',
  remote_pc_input_not_local_presence:'最近操作来自远控，使用手机',
  pc_microphone_not_selected:'尚未选择电脑麦克风', pc_microphone_unavailable:'所选电脑麦克风不可用',
  phone_source_lease_unsupported:'手机服务尚未报告支持采音交接',
  phone_source_lease_unavailable:'无法确认手机采音交接能力',
  pc_mic_silent:'电脑麦克风持续无信号，回退手机', pc_mic_overflow:'电脑采音缓冲溢出，回退手机',
  mode_off:'监听已关闭',
};
const INPUT_LABELS: Record<string,string> = {
  not_started:'尚未启动', unavailable:'线程或注册不可用',
  pump_unresponsive:'消息线程未响应', no_packets:'线程正常，尚未收到输入消息',
  receiving:'消息线程可响应（是否近期收到键鼠见下方）',
};
function ageText(value: unknown): string {
  return value == null ? '未取得' : `${Math.round(Number(value))} 秒前`;
}
function speakerScore(value: unknown): string {
  const speaker = record(value);
  if (speaker.similarity !== undefined) return ` · 相似度 ${Number(speaker.similarity).toFixed(3)}，不是身份概率`;
  const scores = list(speaker.window_candidates).map(item => Number(item.similarity))
    .filter(Number.isFinite);
  return scores.length ? ` · 局部窗口最高相似度 ${Math.max(...scores).toFixed(3)}，不是身份概率` : '';
}

function shortId(value: unknown) {
  const text = String(value || '');
  return text.length > 20 ? `${text.slice(0,9)}…${text.slice(-7)}` : safeText(text);
}

function backgroundText(value: unknown): string {
  const context = record(value);
  const parts: string[] = [];
  const kind = String(context.kind || '');
  if (context.capture_source === 'pc') parts.push(`电脑麦克风${context.capture_device ? `（${safeText(context.capture_device)}）` : ''}`);
  else if (context.capture_source === 'phone') parts.push('手机麦克风');
  if (kind) parts.push(kind === 'voip_or_video_call' ? 'VoIP / 视频通话' : kind === 'cellular_call' ? '蜂窝通话' : kind === 'media_playback' ? '媒体播放' : kind);
  const source = String(context.source_app_name || context.source_app_package || '');
  if (source) parts.push(`来源候选 ${safeText(source)}`);
  if (context.media_playback_active) {
    const media = context.media_source_app_name || context.media_source_app_package;
    parts.push(`媒体播放中${media ? `（${safeText(media)}）` : ''}`);
  }
  const call = context.call_source_app_name || context.call_source_app_package;
  if (call && call !== source) parts.push(`通话来源 ${safeText(call)}`);
  if (context.input_method_visible) {
    const input = context.input_method_app_name || context.input_method_app_package;
    parts.push(`输入法覆盖层可见${input ? `（${safeText(input)}）` : ''}`);
  }
  if (context.screen_on === false) parts.push('息屏');
  if (context.aec_enabled) parts.push('回声消除已请求');
  return parts.length ? parts.join(' · ') : '没有额外设备背景';
}

function decisionBackground(value: unknown): string {
  const rows = list(value);
  return rows.length ? rows.map(backgroundText).join('；') : '没有额外设备背景';
}

function materialState(item: Data): string {
  const reason = String(item.consumed_reason || '');
  if (reason) return MATERIAL_LABELS[reason] || safeText(reason);
  if (item.consumed) return '已进入一次推送判断';
  return MATERIAL_LABELS[String(item.state || '')] || safeText(item.state);
}

export default function AmbientListeningRecords({source}:{source:Source}) {
  const materials = list(source.data?.materials);
  const decisions = list(source.data?.decisions);
  const checkpoints = list(source.data?.checkpoints);
  const runtime = record(source.data?.runtime);
  const captureSource = record(source.data?.source);
  const input = record(captureSource.input);
  const pendingCount = Number(runtime.pending_count || 0);
  const staleCount = Number(runtime.stale_unprocessed_count || 0);
  const pendingMinutes = Math.floor(Number(runtime.oldest_pending_age_seconds || 0) / 60);
  const faults = checkpoints.filter(item => item.status === 'fault' || item.status === 'blocked' || item.status === 'partial').slice(0,8);
  const ready = materials.filter(item => item.state === 'ready').length;
  const duplicates = materials.filter(item => item.consumed_reason === 'voice_dictation_duplicate').length;

  return <section className="monitor-resource-card monitor-resource-wide ambient-monitor">
    <div className="ambient-monitor-heading">
      <div><h3>环境监听记录</h3><p className="monitor-description">主机本地最近 24 小时的材料与明确决策。这里不展示录音文件路径、完整提示词或隐藏推理。</p></div>
      <small>采集于 {timeText(source.updatedAt)}</small>
    </div>
    {source.error && <p className="monitor-inline-error">记录读取失败：{source.error}。已保留上次成功快照。</p>}
    <div className="ambient-monitor-stats" aria-label="环境监听记录摘要">
      <span><strong>{materials.length}</strong> 段材料</span>
      <span><strong>{ready}</strong> 段已分析</span>
      <span><strong>{pendingCount}</strong> 段待分析</span>
      <span><strong>{staleCount}</strong> 段过时跳过</span>
      <span><strong>{Number(runtime.speech_skipped_count || 0)}</strong> 段人声预筛跳过</span>
      <span><strong>{duplicates}</strong> 段语音输入副本</span>
      <span><strong>{decisions.length}</strong> 次推送判断</span>
      <span>当前来源：{captureSource.selected === 'pc' ? '电脑' : captureSource.selected === 'paused' ? '暂停' : '手机'}</span>
    </div>
    <p className="monitor-description">最近收妥材料的采音时间：{timeText(runtime.latest_capture_at)} · 最近有转写文字的采音时间：{timeText(runtime.latest_transcribed_capture_at)}</p>
    <p className="monitor-description">接管原因：{SOURCE_LABELS[String(captureSource.reason)] || safeText(captureSource.reason)}<br/>
      所选电脑麦克风：{safeText(captureSource.configured_microphone || captureSource.pc_microphone || '未选择')}
      {captureSource.pc_error ? ` · 最近采音错误：${safeText(captureSource.pc_error)}` : ''}</p>
    <details><summary>键鼠接管诊断</summary><p className="monitor-description">
      {INPUT_LABELS[String(input.health)] || '当前后端尚未提供详细诊断'}<br/>
      最近本机操作：{ageText(input.physical_idle_seconds)} · 最近远控操作：{ageText(input.remote_idle_seconds)}<br/>
      最近输入消息：{ageText(input.packet_age_seconds)} · 线程响应：{ageText(input.pump_age_seconds)}<br/>
      本次运行收到 {Number(input.packets || 0)} 条输入消息，其中来源未知 {Number(input.unknown_packets || 0)} 条、解析失败 {Number(input.parse_errors || 0)} 条。
      {input.last_error ? ` 错误：${safeText(input.last_error)}` : ''}
    </p></details>
    {pendingCount > 0 && pendingMinutes >= 1 && <p className="monitor-inline-error">本地分析延迟：最早有效片段已等待约 {pendingMinutes} 分钟。过时片段不会再进入主动联系。</p>}
    <div className="ambient-monitor-columns">
      <div><h4>录音材料 / 本地转写</h4>
        {materials.length ? materials.map(item => <article className="ambient-record" key={String(item.clip_id)}>
          <div className="ambient-record-head"><time>{timeText(item.captured_at)}</time><span className={`monitor-state-chip ${item.state === 'failed' || item.state === 'stale' || String(item.consumed_reason || '').startsWith('stale_') ? 'stale' : item.consumed ? 'persistent' : 'fresh'}`}>{materialState(item)}</span></div>
          <p>{item.transcript ? safeText(item.transcript) : item.state === 'failed' ? `未形成转写：${safeText(item.error_code)}` : item.consumed_reason === 'no_speech_detected' ? '短片段整段人声评分极低，本次未运行转写；这不是声纹身份判断。' : '尚未形成有效转写'}</p>
          <small>{backgroundText(item.audio_context)}</small>
          {Boolean(record(item.speech_gate).status) && <small>人声预筛：{safeText(record(item.speech_gate).status)} · {Number(record(item.speech_gate).elapsed_ms || 0)} ms{record(item.speech_gate).peak_score !== undefined ? ` · 最高人声评分 ${Number(record(item.speech_gate).peak_score).toFixed(3)}` : ''}</small>}
          {Boolean(record(item.speaker).basis) && <small>说话人：{record(item.speaker).entity_id === 'unknown' ? '未知' : `${safeText(record(item.speaker).name)}（声纹候选）`} · 依据 {safeText(record(item.speaker).basis)}{speakerScore(item.speaker)}</small>}
          {Boolean(item.message_relation || item.linked_message_id) && <small className="ambient-record-relation">消息关系：{safeText(item.message_relation)} · 匹配度 {Math.round(Number(item.relation_score || 0) * 100)}%</small>}
          <details><summary>查看记录标识与保留状态</summary><p className="monitor-description">片段 {shortId(item.clip_id)} · 音频文件{item.has_audio ? '仍在主机保留' : '已不存在'} · 到期 {timeText(item.expires_at)}</p></details>
        </article>) : <p className="monitor-blank">最近 24 小时尚无录音材料</p>}
      </div>
      <div><h4>推送决策 / 最终结果</h4>
        {decisions.length ? decisions.map(item => <article className="ambient-record" key={String(item.decision_id)}>
          <div className="ambient-record-head"><time>{timeText(item.at)}</time><span className={`monitor-state-chip ${item.participate ? 'fresh' : 'unknown'}`}>{item.participate ? '建议参与' : '不参与'}</span></div>
          <p>{safeText(item.summary || item.event_description || '本次未生成摘要')}</p>
          <small>{OUTCOME_LABELS[String(item.outcome || '')] || safeText(item.outcome)}</small>
          {Boolean(item.reason) && <p className="ambient-record-reason">判断理由：{safeText(item.reason)}</p>}
          <details><summary>查看判断背景</summary><p className="monitor-description">{safeText(item.material_count)} 段 · {safeText(item.material_chars)} 字材料<br/>{decisionBackground(item.device_background)}<br/>批次 {shortId(item.batch_id)}{item.event_id ? ` · 事件 ${shortId(item.event_id)}` : ''}{item.message_id ? ` · 消息 ${shortId(item.message_id)}` : ''}</p></details>
        </article>) : <p className="monitor-blank">最近 24 小时尚未达到推送判断条件</p>}
        {faults.length > 0 && <details className="ambient-faults"><summary>最近链路异常 / 拦截（{faults.length}）</summary><dl className="monitor-facts">{faults.map((item,index)=><div key={`${String(item.at)}-${index}`}><dt>{timeText(item.at)}</dt><dd>{safeText(item.stage)} · {safeText(item.status)}<small>{item.error_code ? ` · ${safeText(item.error_code)}` : ''}{item.next_action ? ` · ${safeText(item.next_action)}` : ''}</small></dd></div>)}</dl></details>}
      </div>
    </div>
  </section>;
}
