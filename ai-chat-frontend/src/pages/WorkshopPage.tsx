import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBase } from '../config';
import WorkshopScene, { type WorkshopTab } from '../workshop/WorkshopScene';
import WorkshopPanel from '../workshop/WorkshopPanel';
import WorkshopReasoning, { type ReasoningOptions } from '../workshop/WorkshopReasoning';
import WorkshopRoom from '../workshop/WorkshopRoom';
import BudgetApproval from '../workshop/WorkshopBudget';
import WorkshopWorks from '../workshop/WorkshopWorks';
import WorkshopIdeas, { type CreativeTemplate } from '../workshop/WorkshopIdeas';
import WorkshopInvite from '../workshop/WorkshopInvite';
import type { Collaboration } from '../workshop/WorkshopCollaboration';
import WorkshopLiveStatus, { liveLabel } from '../workshop/WorkshopLiveStatus';
import { reviewerLabels } from '../workshop/WorkshopQuality';
import type { Artifact, Job, Room, BudgetReference } from '../workshop/types';
import './WorkshopPage.css';

type Settings = { max_parallel:number; reasoning_effort:string; review_reasoning_effort:string; review_model: string; review_token_budget: number; model: string; budget_mode: 'percent' | 'tokens'; remaining_floor: number; token_budget: number; target_percent: number; visit_rounds: number; enabled: boolean; desktop_idle_only:boolean; budget_limits_enabled:boolean };
type Visit = { id: string; state: string; summary: string; job_id?: string };
type State = { execution_pool?:{capacity:number;running:{job_id:string;kind:string}[]}; workshop_account_usage?:{bucket:string;observed_percent:number|null;window_mins?:number;active:boolean}[]; creative_templates?: CreativeTemplate[]; settings: Settings; desktop_activity?:{status:string;ready:boolean;active_tasks:number;quiet_seconds_left?:number}; quota: { remaining: number | null; checked_at: number | null; status: string; models: string[]; review_models?:string[]; reasoning_options?:ReasoningOptions }; jobs: Job[]; total_jobs: number; archived_jobs_count?: number; has_more: boolean; current_job_id: string | null; capabilities: Record<string, string>; budget_references?: Record<string, BudgetReference | null>; budget_note: string };
const modelLabels: Record<string, string> = { 'gpt-6-astra': '6.0 Astra', 'gpt-6.1-sol': '6.1 Sol' };
const capabilityLabels: Record<string, string> = { files_shell_git: '文件与执行', web_search: '联网检索', browser: '浏览器', image_generation: '图像创作', delivery: '作品交付', desktop_sidebar_sync: '桌面记录' };
export const workshopState: Record<string, string> = { queued: '已接受 · 排队', account_paused: '等待开工条件恢复', discussing: '讨论中', awaiting_input: '等待 AI 回访', awaiting_confirmation: '等待 AI 确认方案', executing: '制作中', awaiting_review: '等待 AI 审阅', budget_paused: '预算暂停', paused: '已暂停', stopping: '正在保存现场并停止', recovery_required: '现场待核对', accepted: 'AI 已验收', canceled: '已取消' };

async function read<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBase()}/api/workshop${path}`, body === undefined ? {signal:AbortSignal.timeout(8000)} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop-UI': 'settings' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '工作坊暂时无法读取');
  return data as T;
}

export default function WorkshopPage({ onBack, initialJobId = '', dark = false }: { onBack: () => void; initialJobId?: string; dark?: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [checkedAt,setCheckedAt]=useState<number|null>(null);
  const [syncError,setSyncError]=useState('');
  const loadRequest=useRef(0);
  const loadInFlight=useRef<{offset:number;request:number}|null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [tab, setTab] = useState<'orders' | 'works' | 'settings'>('orders');
  const [panelOpen, setPanelOpen] = useState(Boolean(initialJobId));
  const [inviteDraft,setInviteDraft]=useState<string|null>(null);
  const [inviteJobId,setInviteJobId]=useState('');
  const [collaboration,setCollaboration]=useState<Collaboration|null>(null);
  const [selected, setSelected] = useState(initialJobId);
  const [roomOrigin, setRoomOrigin] = useState<'orders' | 'works'>('orders');
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [room, setRoom] = useState<Room | null>(null);
  const [roomLoading, setRoomLoading] = useState(Boolean(initialJobId));
  const [roomError, setRoomError] = useState('');
  const roomRequest = useRef(0);
  const roomInFlight = useRef<{ request: number; key: string } | null>(null);
  const [offset, setOffset] = useState(0);
  const [orderOffset, setOrderOffset] = useState(0);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [visit, setVisit] = useState<Visit | null>(null);
  const [inviting, setInviting] = useState(false);
  const [notice, setNotice] = useState('');
  const [dismissedNotice, setDismissedNotice] = useState('');
  const invitationInFlight = useRef(false);
  useEffect(() => {
    if (initialJobId) { setSelected(initialJobId); setOffset(0); setPreview(null); setRoom(null); setRoomLoading(true); setPanelOpen(true); }
  }, [initialJobId]);
  const load = useCallback(async () => {
    if(loadInFlight.current?.offset===orderOffset && loadInFlight.current.request===loadRequest.current)return;
    const request=++loadRequest.current;
    loadInFlight.current={offset:orderOffset,request};
    try {
      const value = await read<State>(`?offset=${orderOffset}`);
      if(request!==loadRequest.current)return;
      setState(value);
      setCheckedAt(Date.now());
      setSyncError('');
      setVisit((value as State & { visit?: Visit }).visit || null);
      setCollaboration((value as State & {collaboration?:Collaboration}).collaboration || null);
      setSettings(previous => previous || value.settings);
    } catch (cause) { if(request===loadRequest.current){const message=cause instanceof Error ? cause.message : '工作坊读取失败';setSyncError(message);} }
    finally {if(loadInFlight.current?.request===request)loadInFlight.current=null;}
  }, [orderOffset]);
  const loadRoom = useCallback(async () => {
    if (!selected) return;
    const key = `${selected}:${offset}`;
    if (roomInFlight.current?.key === key && roomInFlight.current.request === roomRequest.current) return;
    const request = ++roomRequest.current;
    roomInFlight.current = { request, key };
    try {
      const value = await read<Room>(`/rooms/${selected}?offset=${offset}&flow_version=2`);
      if (request === roomRequest.current) { setRoom(value); setRoomError(''); }
    } catch (cause) {
      if (request === roomRequest.current) setRoomError(cause instanceof Error ? cause.message : '工单详情读取失败');
    } finally {
      if (request === roomRequest.current) setRoomLoading(false);
      if (roomInFlight.current?.request === request) roomInFlight.current = null;
    }
  }, [selected, offset]);
  useEffect(() => {
    if (!selected) return;
    void loadRoom();
    return () => { roomRequest.current += 1; };
  }, [selected, loadRoom]);
  useEffect(() => {
    const sync=()=>{if(!document.hidden){void load();void loadRoom();}};
    sync();const timer=window.setInterval(sync,2000);
    document.addEventListener('visibilitychange',sync);window.addEventListener('focus',sync);
    return()=>{window.clearInterval(timer);document.removeEventListener('visibilitychange',sync);window.removeEventListener('focus',sync);loadRequest.current+=1;};
  },[load,loadRoom]);
  useEffect(() => { void read<State>('/refresh', {}).catch(cause => setError(String(cause))); }, []);
  const mutate = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); await load(); } catch (cause) { setError(cause instanceof Error ? cause.message : '操作未完成'); }
    finally { setBusy(false); }
  };
  const contribute = async (text: string, requestId: string) => {
    if (!room || room.job.id !== selected) throw new Error('请先读完当前工单');
    try {
      await read(`/rooms/${selected}/inspiration`, { text, revision: room.job.revision, request_id: requestId });
    } catch (cause) {
      if (selectedRef.current === selected) await loadRoom();
      throw cause;
    }
    await load();
    if (selectedRef.current === selected) {
      roomRequest.current += 1;
      roomInFlight.current = null;
      await loadRoom();
    }
  };
  const chosen = settings?.model || state?.settings.model || 'gpt-6.1-sol';
  const invite = async (templateId?: string, text?:string) => {
    if (invitationInFlight.current || visit?.state === 'visiting' || collaboration?.active) return;
    invitationInFlight.current = true;
    setInviting(true);
    try {
      const receipt = await read<{ started: boolean; visit: Visit; collaboration?:Collaboration }>('/invite', { ...(templateId?{template_id:templateId}:{}), ...(text?.trim()?{text:text.trim()}:{}), ...(inviteJobId?{job_id:inviteJobId}:{}) });
      setVisit(receipt.visit);
      setCollaboration(receipt.collaboration || null);
      setInviteJobId('');
      setInviteDraft(null);
      setNotice('合作已接上，快捷邀请已入聊天。等待时可聊天，新进展会自动交还 AI。');
      setError('');
    } catch (cause) { setNotice('这次邀请尚未保存或启动，请查看原因。');setError(cause instanceof Error ? cause.message : '邀请没有发出'); }
    finally { invitationInFlight.current = false; setInviting(false); }
  };
  const openPanel = (value: WorkshopTab) => { setTab(value); setPanelOpen(true); };
  const openRoom = (id: string, origin: 'orders' | 'works' = 'orders') => { setRoomOrigin(origin); setSelected(id); setOffset(0); setPreview(null); setRoom(null); setRoomError(''); setRoomLoading(true); };
  const liveJob=state?.jobs.find(job=>job.id===selected) || state?.jobs.find(job=>state.execution_pool?.running.some(lane=>lane.job_id===job.id)) || state?.jobs.find(job=>!['accepted','canceled'].includes(job.state)) || state?.jobs[0];
  const poolProps={pool:state?.execution_pool,jobs:state?.jobs,account:state?.workshop_account_usage,collaboration};
  const liveStatus=<WorkshopLiveStatus {...poolProps} job={liveJob} desktop={state?.desktop_activity} idleOnly={state?.settings.desktop_idle_only ?? true} checkedAt={checkedAt} error={syncError}/>;
  return <main className="workshop-page" data-theme={dark ? 'dark' : 'light'}>
    <WorkshopScene paused={panelOpen || inviteDraft!==null} reviewerLabel={reviewerLabels[settings?.review_model || "gpt-6-luna"] || "6.0 Luna"} model={chosen} label={modelLabels[chosen]} remaining={state?.quota.remaining ?? null} checkedAt={state?.quota.checked_at ?? null} floor={state?.settings.remaining_floor ?? 30} total={state?.total_jobs ?? 0} onOpen={openPanel} onBack={onBack} onInvite={() => {if(collaboration?.active){openPanel('orders');return;}setInviteJobId('');setError('');setInviteDraft('');}} collaborating={Boolean(collaboration?.active)} inviting={inviting || visit?.state === 'visiting'} inviteDisabled={!state || !state.settings.enabled} onRefresh={() => void mutate(() => read('/refresh', {}))} refreshing={busy} />
    {!panelOpen && <WorkshopLiveStatus {...poolProps} job={liveJob} desktop={state?.desktop_activity} idleOnly={state?.settings.desktop_idle_only ?? true} checkedAt={checkedAt} error={syncError} floating/>}
    {(notice || visit) && !panelOpen && dismissedNotice !== (visit?.summary || notice) && <div className="workshop-visit-notice" role="status"><span>{visit?.summary || notice}</span><button aria-label="收起来访提示" onClick={() => setDismissedNotice(visit?.summary || notice)}>×</button></div>}
    {error && !panelOpen && <p className="workshop-error" role="alert">{error}</p>}
    {panelOpen && <WorkshopPanel tab={tab} onTab={setTab} onClose={() => setPanelOpen(false)} busy={busy} onRefresh={() => void mutate(() => read('/refresh', {}))}>
        {error && <p className="workshop-error" role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}        {tab === 'settings' && settings && <form className="workshop-settings" onSubmit={event => { event.preventDefault(); void mutate(() => read('/settings', settings)); }}>
          <label>工具手模型<select value={chosen} onChange={event => setSettings({ ...settings, model: event.target.value })}>{Object.entries(modelLabels).map(([id, label]) => <option key={id} value={id} disabled={!state?.quota.models.includes(id)}>{label}{!state?.quota.models.includes(id) ? ' · 尚未确认可用' : ''}</option>)}</select></label>
          <WorkshopReasoning label="工具手推理强度" model={chosen} value={settings.reasoning_effort || 'high'} options={state?.quota.reasoning_options} onChange={value=>setSettings({...settings,reasoning_effort:value})}/>
          <WorkshopReasoning label="Luna 质检推理强度" model={settings.review_model} value={settings.review_reasoning_effort || 'medium'} options={state?.quota.reasoning_options} onChange={value=>setSettings({...settings,review_reasoning_effort:value})}/>
          <p>默认制作高、质检中。保存后用于下一次新回合，正在执行的回合保持原设置；实际强度写入工单回执。更高强度可能增加耗时和用量。</p>
          <label>独立质检助手<select value={settings.review_model || "gpt-6-luna"} onChange={event => setSettings({ ...settings, review_model: event.target.value })}>{Object.entries(reviewerLabels).map(([id,label]) => <option key={id} value={id} disabled={!state?.quota.review_models?.includes(id)}>{label}{!state?.quota.review_models?.includes(id) ? " · 尚未确认可用" : ""}</option>)}</select></label><p>交付 → Luna 独立质检 → AI 验收。两个 Luna 版本共用形象，版本显示在小木牌；新选择用于新交付。</p><details><summary>质检资源保护</summary><label>每件作品质检累计 token 上限<input type="number" min="1000" max="100000" step="1000" value={settings.review_token_budget || 30000} onChange={event => setSettings({ ...settings, review_token_budget: Number(event.target.value) })} /></label><p>质检独立记账，同件作品重试累计消耗；不混进制作模型的百分比估算。账号开工门槛覆盖双方；首次百分比校准时质检等候。</p></details><label>开工最低剩余额度 %<input type="number" min="0" max="100" value={settings.remaining_floor} onChange={event => setSettings({ ...settings, remaining_floor: Number(event.target.value) })} /></label>
          <label>每张工单的额度目标（估算 %）<input type="number" min="0.1" max="100" step="0.1" value={settings.target_percent} onChange={event => setSettings({ ...settings, budget_mode: 'percent', target_percent: Number(event.target.value) })} /></label>
          <p>只按 AI 工单自己的用量估算，参考官方长额度窗口；你的桌面消耗只影响账号总门槛。</p>
          <p>{state?.budget_references?.[chosen] ? `当前模型已有 ${state.budget_references[chosen]?.sample_count} 个独立参考样本，仍属于估算。` : '当前模型待校准。首次手动批准时可同时校准，无需你填写 token 数。'}</p>
          <details><summary>兼容旧工单的高级设置</summary><label>预算方式<select value={settings.budget_mode || 'percent'} onChange={event => setSettings({ ...settings, budget_mode: event.target.value as Settings['budget_mode'] })}><option value="percent">AI 工单百分比估算</option><option value="tokens">原 token 预算</option></select></label><label>原 token 预算<input type="number" min="1000" max="10000000" step="1000" value={settings.token_budget} onChange={event => setSettings({ ...settings, token_budget: Number(event.target.value) })} /></label></details>
          <label>每段回应最多行动数<input type="number" min="1" max="8" value={settings.visit_rounds} onChange={event => setSettings({ ...settings, visit_rounds: Number(event.target.value) })} /></label><p>合作会持续到成品处置或明确卡点；这个上限只控制每段回应，等待制作不占聊天。</p>
          <label><input type="checkbox" checked={settings.enabled} onChange={event => setSettings({ ...settings, enabled: event.target.checked })} />开放工作坊与自动开工</label>
          <label><input type="checkbox" checked={settings.budget_limits_enabled ?? true} onChange={event=>setSettings({...settings,budget_limits_enabled:event.target.checked})}/>执行工单预算限制</label>
          <p>{settings.budget_limits_enabled?'制作与质检按各自已有预算准入。':'链路测试：预算限制暂时关闭，制作与质检实际用量仍保留。'}</p>
          <label>同时开工数<select value={settings.max_parallel ?? 1} onChange={event=>setSettings({...settings,max_parallel:Number(event.target.value)})}><option value={1}>1 单 · 逐单执行</option><option value={2}>2 单 · 并行执行</option></select></label>
          <p>制作与 Luna 质检共用名额；同一单依次推进，等待 AI 时释放名额。调低数量会等当前任务结束。</p>
          {settings.max_parallel===2 && <p>每单保留实际 token 回执；空闲模式下账号下降统一记录在工作坊，不分摊到单单。</p>}
          <label>开工方式<select value={settings.desktop_idle_only?'idle':'direct'} onChange={event=>setSettings({...settings,desktop_idle_only:event.target.value==='idle'})}><option value="idle">Codex 空闲时开工</option><option value="direct">直接开工 · 不统计额度下降</option></select></label>
          <p>{settings.desktop_idle_only?'桌面任务开始时，制作与 Luna 质检保存现场并等候；空闲后自动续跑。只观测实际开工段的账号额度下降。':'可与桌面 Codex 同时运行，不统计账号额度下降；历史统计保留，实际 token 回执继续记录。账号最低剩余额度、并发上限和方案确认仍有效。'}</p>
          {settings.desktop_idle_only && state?.desktop_activity && <p>本机桌面：{state.desktop_activity.status==='busy'?`${state.desktop_activity.active_tasks} 个任务正在执行`:state.desktop_activity.status==='unknown'?'运行状态待核对':state.desktop_activity.ready?'空闲，可以开工':`空闲，等候 ${Math.ceil(state.desktop_activity.quiet_seconds_left || 0)} 秒稳定回执`}</p>}
          <p>{state?.budget_note}已开工工单沿用各自模型和累计预算，新设置在新工单开工时生效。</p>
          <button disabled={busy} type="submit">保存工作坊设置</button>
          <details className="workshop-capabilities"><summary>工具手目前能做什么</summary><dl>{Object.entries(state?.capabilities || {}).map(([key, value]) => <div key={key}><dt>{capabilityLabels[key] || key}</dt><dd>{value}</dd></div>)}</dl></details>
          {state?.jobs.filter(job => job.state === 'budget_paused').map(job => <BudgetApproval key={job.id} job={job} reference={state.budget_references?.[job.model || chosen]} busy={busy} onApprove={body => void mutate(async () => { await read(`/rooms/${job.id}/budget`, body); setNotice(state.budget_references?.[job.model || chosen] || job.budget_summary?.status === 'estimated' ? '追加额度批准已保存，等待账号门槛与队列；桌面用量不计入本工单。' : '追加额度批准已保存，等待账号门槛与队列。首次校准期间请暂停其他 Codex 的模型调用。'); })} />)}
        </form>}
        {tab === 'works' && <WorkshopWorks apiBase={getApiBase()} onRoom={id => { setTab('orders'); openRoom(id, 'works'); }} />}
        {tab === 'orders' && <>
          {liveStatus}
          {selected && room?.job.visibility==='public' && !collaboration?.active && <button className="workshop-continue" disabled={inviting || visit?.state==='visiting'} onClick={()=>{setInviteJobId(selected);setError('');setInviteDraft('existing_upgrade');}}>请 AI 接着这一单合作</button>}
          {visit && <p role="status">{visit.summary}</p>}
          {!selected && <WorkshopIdeas templates={state?.creative_templates || []} onInvite={id => {setInviteJobId('');setError('');setInviteDraft(id);}} busy={inviting || visit?.state === 'visiting' || Boolean(collaboration?.active) || !state?.settings.enabled} />}
          {!selected && <><div className="workshop-orders">{state?.jobs.map(job => <button key={job.id} disabled={job.visibility === 'private'} onClick={() => openRoom(job.id)}><strong>{job.visibility === 'private' ? '🔒 ' : ''}{job.title}</strong><span>{liveLabel(job,state.desktop_activity,state.settings.desktop_idle_only)}{job.position ? ` · 第 ${job.position} 位` : ''}</span><small>{job.waiting_reasons?.join('；') || job.reason || '查看完整请求、对话与交付 →'}</small></button>)}{state?.jobs.length === 0 && <p>{state.archived_jobs_count ? '已验收工单已归档到作品柜，仍可欣赏历史作品或补充灵感继续升级。' : 'AI 还没留下工单。可以邀请他来坐坐，或等他下次漫想来访。'}</p>}{!!state?.archived_jobs_count && <button onClick={() => setTab('works')}>📚 作品柜历史 · {state.archived_jobs_count} 单</button>}</div>
          {state && state.total_jobs > 50 && <div className="workshop-pagination"><button disabled={orderOffset === 0} onClick={() => setOrderOffset(Math.max(0, orderOffset-50))}>前 50 单</button><span>共 {state.total_jobs} 单 · 当前从第 {orderOffset+1} 单展示</span><button disabled={!state.has_more} onClick={() => setOrderOffset(orderOffset+50)}>后 50 单</button></div>}</>}
          {selected && <WorkshopRoom room={room} fallback={state?.jobs.find(job => job.id === selected)} desktop={state?.desktop_activity} idleOnly={state?.settings.desktop_idle_only} loading={roomLoading} error={roomError} tab={tab} labels={workshopState} modelLabels={modelLabels} offset={offset} onOffset={setOffset} backLabel={roomOrigin === 'works' ? '返回藏品' : '返回工单列表'} onBack={() => { setSelected(''); setRoom(null); setPreview(null); setTab(roomOrigin); }} onRetry={() => { setRoomLoading(true); void loadRoom(); }} preview={preview} onPreview={setPreview} apiBase={getApiBase()} onContribute={contribute} />}
        </>}
    </WorkshopPanel>}
    {inviteDraft!==null && <WorkshopInvite templates={state?.creative_templates || []} initialTemplate={inviteDraft} busy={inviting} error={error} onClose={()=>setInviteDraft(null)} onSend={(template,text)=>void invite(template,text)}/>}
  </main>;
}
