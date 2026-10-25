import { useEffect, useRef, useState } from 'react';
import { getApiBase } from '../config';
import { startEnrollmentRecording, type EnrollmentRecording } from '../lib/voiceEnrollmentAudio';
import { cognitionRequest } from './cognitionApi';

type Sample = { sample_id: string; entity_id: string; label: string; source: string };
type Status = { available: boolean; reason: string; samples: Sample[] };
type Draft = { draft_id: string; duration_seconds: number; consistency: number; source: string };

type EntityMap = Record<string, { id: string; name: string; type: string }>;
export default function VoiceIdentityEnrollment({ entities: supplied }: { entities?: EntityMap }) {
  const [loadedEntities, setLoadedEntities] = useState<EntityMap>({});
  const entities = supplied || loadedEntities;
  const [status, setStatus] = useState<Status | null>(null);
  const [target, setTarget] = useState('human');
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [device, setDevice] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [message, setMessage] = useState('');
  const handle = useRef<EnrollmentRecording | null>(null);
  const starting = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const base = () => `${getApiBase()}/api/ambient-listening`;
  async function request(path: string, init?: RequestInit) {
    const response = await fetch(`${base()}${path}`, init);
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || `请求失败 ${response.status}`);
    return value;
  }
  async function refresh() { const value = await request('/voice-identity'); if (alive.current) setStatus(value); }
  async function loadDevices() {
    const list = await navigator.mediaDevices?.enumerateDevices();
    if (alive.current) setDevices((list || []).filter(item => item.kind === 'audioinput'));
  }
  useEffect(() => {
    alive.current = true;
    void refresh().catch(e => setMessage(e.message));
    if (!supplied) void cognitionRequest('/other').then(data => {
      if (alive.current) setLoadedEntities(data.entities);
    }).catch(e => { if (alive.current) setMessage(e.message); });
    void loadDevices().catch(() => {});
    const hidden = () => { if (document.hidden) void stop(true); };
    document.addEventListener('visibilitychange', hidden);
    return () => {
      alive.current = false; document.removeEventListener('visibilitychange', hidden);
      if (timer.current) clearInterval(timer.current);
      void handle.current?.stop(true); handle.current = null;
    };
  }, []);
  useEffect(() => {
    const url = audio ? URL.createObjectURL(audio) : '';
    setAudioUrl(url);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [audio]);
  async function start() {
    if (starting.current || handle.current || busy) return;
    starting.current = true; setBusy(true); setMessage(''); setDraft(null); setAudio(null); setConfirmed(false);
    try {
      const policy = await request('/status');
      if (policy.capture.mode !== 'off') throw new Error('请先把环境监听切为“关闭”，避免登记时两个录音器争用麦克风；录完可恢复智能管理');
      const started = await startEnrollmentRecording(device);
      if (!alive.current || document.hidden) { await started.stop(true); return; }
      handle.current = started; setRecording(true); setSeconds(0);
      void loadDevices().catch(() => {});
      const began = Date.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - began) / 1000);
        setSeconds(elapsed);
        if (elapsed >= 25) void stop(false);
      }, 200);
    } catch (e) { if (alive.current) setMessage((e as Error).message); }
    finally { starting.current = false; if (alive.current) setBusy(false); }
  }
  async function stop(cancel: boolean) {
    const current = handle.current;
    if (!current) return;
    handle.current = null;
    if (timer.current) { clearInterval(timer.current); timer.current = null; }
    setRecording(false); setBusy(true);
    try {
      const blob = await current.stop(cancel);
      if (!blob || !alive.current) return;
      setAudio(blob);
      const form = new FormData(); form.append('file', blob, 'enrollment.wav'); form.append('source', current.source);
      const result = await request('/voice-identity/prepare', { method: 'POST', body: form });
      if (alive.current) { setDraft(result); setMessage('检查完成，试听后确认人物归属。尚未绑定。'); }
    } catch (e) { if (alive.current) setMessage((e as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }
  async function confirm() {
    if (!draft || !confirmed || busy) return;
    setBusy(true); setMessage('');
    try {
      await request('/voice-identity/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_id: draft.draft_id, entity_id: target, confirmed_single_speaker: true }) });
      setDraft(null); setAudio(null); setConfirmed(false); await refresh();
      setMessage('绑定已保存到本地主机。可再用另一支麦克风补录，然后恢复智能监听测试。');
    } catch (e) { setMessage((e as Error).message); } finally { setBusy(false); }
  }
  async function remove(sample: Sample) {
    if (!window.confirm(`删除“${sample.label}”的这份声纹样本（${sample.source}）？需要重新录入才能恢复。`)) return;
    setBusy(true);
    try { await request(`/voice-identity/${sample.sample_id}`, { method: 'DELETE' }); await refresh(); }
    catch (e) { setMessage((e as Error).message); } finally { setBusy(false); }
  }
  return <section aria-label="声纹录入">
    <p>声纹仅存本地主机，不写入他者书正文、不发送外部模型。匹配是身份候选，混合讲话尚不逐句分离。</p>
    {!status ? <p>正在读取模型状态…</p> : !status.available ? <p>本地声纹模型未就绪：{status.reason}</p> : <>
      <p>先关闭环境监听；安静环境独自自然讲话 15–25 秒。手机与 HECATE 耳机建议各录一份。录音检查不会转写、不会作为聊天发送。</p>
      <label>绑定人物 <select value={target} disabled={busy || recording} onChange={e => { setTarget(e.target.value); setConfirmed(false); }}>
        {Object.values(entities).filter(e => e.type === 'human').map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
      </select></label>
      {!window.MirrowAudio && <label> 录入麦克风 <select value={device} disabled={busy || recording} onChange={e => setDevice(e.target.value)}>
        <option value="">系统默认输入（开始后显示实际来源）</option>
        {devices.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `麦克风 ${i + 1}`}</option>)}
      </select></label>}
      <div style={{ marginTop: 8 }}>
        {recording ? <><span>录音中 {seconds}s · 25 秒自动结束 </span><button onClick={() => void stop(false)}>结束并检查</button><button onClick={() => void stop(true)}>取消</button></>
          : <button disabled={busy || !entities[target]} onClick={() => void start()}>{busy ? '处理中…' : draft ? '重新录音' : '开始录音'}</button>}
      </div>
      {audioUrl && <audio controls src={audioUrl} style={{ maxWidth: '100%', marginTop: 8 }} />}
      {draft && <div><p>实际来源：{draft.source} · 时长 {draft.duration_seconds}s · 段间一致性 {draft.consistency}（不是身份概率）</p>
        <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />我确认整段只有“{entities[target]?.name || target}”本人讲话，没有其他人或音乐混入</label>
        <button disabled={busy || !confirmed} onClick={() => void confirm()}>确认绑定</button>
      </div>}
    </>}
    {message && <p role="status">{message}</p>}
    <div>{status?.samples.length ? status.samples.map(sample => <p key={sample.sample_id}>{sample.label} · {sample.source} <button disabled={busy || recording} onClick={() => void remove(sample)}>删除此样本</button></p>) : <p>尚无已确认声纹样本。</p>}</div>
  </section>;
}
