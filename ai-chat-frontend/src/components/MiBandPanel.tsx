import React, { useEffect, useState, useRef } from 'react';
import { Capacitor, registerPlugin } from '@capacitor/core';
import { getApiBase } from '../config';

interface NativeBand {
  execute(args: Record<string, unknown>): Promise<{ success: boolean; content?: string; data: any }>;
  scan(): Promise<{ devices: { address: string; name: string }[]; message: string }>;
  configure(args: { address: string; name: string }): Promise<void>;
}
const native = registerPlugin<NativeBand>('MiBand');
const box: React.CSSProperties = { padding: 14, marginBottom: 14, border: '1px solid #d9c7b8', borderRadius: 14, background: '#fff8ef', color: '#604b3b', overflowWrap: 'anywhere' };

export default function MiBandPanel() {
  const [status, setStatus] = useState<any>(null);
  const [data, setData] = useState<any>(null);
  const [devices, setDevices] = useState<{ address: string; name: string }[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const scheduleLoaded = useRef(false);
  const [schedule, setSchedule] = useState({ day_interval: 5, night_interval: 20, night_start: '02:00', night_end: '08:00' });
  const isNative = Capacitor.isNativePlatform();
  const refresh = async () => {
    const response = await fetch(`${getApiBase()}/api/health/mi-band`);
    if (response.ok) setData(await response.json());
    if (isNative) {
      const result = await native.execute({ action: 'status' });
      setStatus(result.data);
      if (!scheduleLoaded.current && result.data) {
        const { day_interval, night_interval, night_start, night_end } = result.data;
        setSchedule({ day_interval, night_interval, night_start, night_end });
        scheduleLoaded.current = true;
      }
    }
  };
  useEffect(() => {
    refresh().catch(() => setMessage('新版手环模块尚未就绪，请确认 APK 和后端均已更新'));
    const timer = window.setInterval(() => refresh().catch(() => {}), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const run = async (job: () => Promise<void>) => {
    setBusy(true); setMessage('');
    try { await job(); await refresh(); }
    catch (e: any) { setMessage(e?.message || '操作失败'); }
    finally { setBusy(false); }
  };
  const action = (value: string, extra = {}) => run(async () => {
    const result = await native.execute({ action: value, ...extra });
    setMessage(result.content || (result.success ? '已提交' : '未执行'));
  });
  const time = (value: number) => value ? new Date(value * 1000).toLocaleString() : '尚无记录';
  return <section style={box} aria-label="小米手环7">
    <strong>⌚ 小米手环 7 · 手腕上的联系</strong>
    <p style={{ fontSize: 13 }}>手机负责蓝牙连接，健康数据同步给 MIRROW。手环步数与手机步数分开记录。</p>
    {isNative ? <>
      <p>{status?.device_name || '尚未选择手环'} · {status?.state || '未就绪'} · 密钥{status?.auth_key_configured ? '已保存 ****' : '未配置'}{status?.battery >= 0 ? ` · 电量 ${status.battery}%` : ''}</p>
      <p style={{ fontSize: 12 }}>上次同步：{time(status?.last_sync_at)}；下次：{time(status?.next_sync_at)}</p>
      {status?.error && <p role="status">{status.error}</p>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button disabled={busy} onClick={() => run(async () => { const r = await native.scan(); setDevices(r.devices); setMessage(r.message); })}>扫描／授权</button>
        {['connect','disconnect','sync','realtime_start','realtime_stop'].map((x, i) => <button key={x} disabled={busy || !status?.configured} onClick={() => action(x)}>{['连接','断开','同步历史','开始实时心率','停止实时心率'][i]}</button>)}
      </div>
      {devices.map(d => <button key={d.address} disabled={busy} style={{ display: 'block', marginTop: 8 }} onClick={() => run(async () => { await native.configure(d); setMessage('已保存本机配置，正在连接'); })}>{d.name || '手环'} · {d.address.slice(-5)} · 选择并填写密钥</button>)}
      <details style={{ marginTop: 12 }}><summary>同步计划与手环测试</summary>
        <p style={{ fontSize: 12 }}>实时心率与历史同步互斥；同步前先停止实时心率。</p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {(['day_interval','night_interval'] as const).map(k => <label key={k}>{k === 'day_interval' ? '白天' : '夜间'}<select value={schedule[k]} onChange={e => setSchedule({ ...schedule, [k]: Number(e.target.value) })}>{[-1,1,5,10,20,30,60].map(n => <option value={n} key={n}>{n === -1 ? '暂停' : `${n}分钟`}</option>)}</select></label>)}
          {(['night_start','night_end'] as const).map(k => <label key={k}>{k === 'night_start' ? '夜间开始' : '夜间结束'}<input type="time" value={schedule[k]} onChange={e => setSchedule({ ...schedule, [k]: e.target.value })}/></label>)}
          <button disabled={busy} onClick={() => action('schedule', schedule)}>保存计划</button>
        </div>
        <button disabled={busy || !status?.authenticated} onClick={() => action('vibrate', { pattern: 'single' })}>测试轻震一次</button>
        <input aria-label="手环测试纸条" placeholder="测试纸条（80字内）" maxLength={80} value={note} onChange={e => setNote(e.target.value)} style={{ width: '100%', boxSizing: 'border-box', marginTop: 8 }}/>
        <button disabled={busy || !note.trim() || !status?.authenticated} onClick={() => action('note', { pattern: 'single', note })}>发送测试纸条</button>
      </details>
    </> : <p>请在手机 MIRROW App 的此面板扫描与填写密钥；网页可查看已同步数据。</p>}
    {message && <p role="status">{message}</p>}
    <details style={{ marginTop: 12 }}><summary>已同步健康数据与来源</summary>
      {data?.available ? <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(data, null, 2)}</pre> : <p>尚未收到真实手环数据。</p>}
      <p style={{ fontSize: 12 }}>血氧、压力、PAI、呼吸率：当前协议采集尚未接通。睡眠是设备历史分期，不代表当前已入睡。</p>
    </details>
  </section>;
}
