import React, { useState, useEffect, useRef } from 'react';
import { getApiBase } from '../config';
import type { PerceptionSettings } from '../hooks/useSentinel';
import { searchCities } from '../data/china-cities';
import GpsPlaceSettings from './GpsPlaceSettings';

/**
 * 硅基感知配置页（哨兵面板 ⚙️设置 tab）
 * 集中：天气监控城市 + 家/公司坐标 + 作息时间。
 * 感知/心率总开关在互动级别设置页；高德 Key 统一在后端 .env。
 * 所有保存走 POST /api/settings {key,value}（后端双写 SQLite）。
 */

interface Props {
  settings: PerceptionSettings;
  saveSettings: (s: PerceptionSettings) => Promise<boolean> | void;
  showToast: (m: string) => void;
}

const lbl: React.CSSProperties = { fontSize: '0.8em', color: '#888', display: 'block', marginBottom: 2 };
const inp: React.CSSProperties = { width: '100%', padding: '3px 6px', borderRadius: 3, border: '1px solid #666', background: 'transparent', color: 'inherit', fontSize: '0.85em', boxSizing: 'border-box' };
const btnSm: React.CSSProperties = { fontSize: '0.75em', padding: '2px 8px', borderRadius: 3, border: '1px solid #888', cursor: 'pointer', background: 'transparent', color: 'inherit' };
const section: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, background: 'rgba(0,0,0,0.03)', marginBottom: 10, border: '1px solid rgba(0,0,0,0.06)' };

type AmbientThresholds = {
  idle_minutes: number;
  immediate_chars: number;
  timed_min_chars: number;
  timed_seconds: number;
  cooldown_seconds: number;
};

const defaultAmbientThresholds: AmbientThresholds = {
  idle_minutes: 10,
  immediate_chars: 500,
  timed_min_chars: 80,
  timed_seconds: 120,
  cooldown_seconds: 180,
};

const sourceReasonLabels: Record<string, string> = {
  recent_local_pc_input: '近期有本机键鼠操作',
  no_recent_local_pc_input: '近期没有本机键鼠操作',
  remote_pc_input_not_local_presence: '最近是远程操控，不视为在电脑旁',
  pc_input_origin_unavailable: '电脑在场线索不可用',
  pc_microphone_not_selected: '尚未选择电脑麦克风',
  pc_microphone_unavailable: '所选电脑麦克风不可用',
  phone_source_lease_unsupported: '手机 App 尚未升级到双端切换版',
  phone_source_lease_unavailable: '无法确认手机切换能力',
  pc_microphone_start_failed: '电脑麦克风启动失败',
  PortAudioError: '电脑麦克风打开失败，已回到手机',
  pc_mic_silent: '电脑麦克风持续无声，已回到手机',
  pc_mic_overflow: '电脑麦克风连续丢帧，已回到手机',
};

export const PerceptionSettingsTab: React.FC<Props> = ({ settings, saveSettings, showToast }) => {
  const weather = settings.weather || { city: '', auto_refresh_location: false };
  const [cityInput, setCityInput] = useState(weather.city || '');
  const [showCityList, setShowCityList] = useState(false);
  const [locating, setLocating] = useState(false);
  const cityBoxRef = useRef<HTMLDivElement>(null);

  // 作息（独立保存路径：POST /api/settings key=user_schedule）
  const [schedule, setSchedule] = useState({ work_start: '', work_end: '', lunch_start: '', lunch_end: '' });
  const [wfhToday, setWfhToday] = useState(false);
  const [ambientMode, setAmbientMode] = useState<'off' | 'smart' | 'on'>('smart');
  const [ambientStatus, setAmbientStatus] = useState<any>(null);
  const [ambientBusy, setAmbientBusy] = useState(false);
  const [ambientThresholds, setAmbientThresholds] = useState<AmbientThresholds>(defaultAmbientThresholds);
  const [ambientThresholdBusy, setAmbientThresholdBusy] = useState(false);
  const [pcMicrophones, setPcMicrophones] = useState<Array<{ name: string; sample_rate: number }>>([]);
  const [pcMicrophone, setPcMicrophone] = useState('');
  const [pcMicrophoneBusy, setPcMicrophoneBusy] = useState(false);
  useEffect(() => {
    fetch(`${getApiBase()}/api/settings`).then(r => r.json()).then(d => {
      if (d.user_schedule) setSchedule(d.user_schedule);
      if (['off', 'smart', 'on'].includes(d.ambient_listening_mode)) setAmbientMode(d.ambient_listening_mode);
      if (d.ambient_listening_thresholds) setAmbientThresholds({ ...defaultAmbientThresholds, ...d.ambient_listening_thresholds });
      if (typeof d.ambient_pc_microphone === 'string') setPcMicrophone(d.ambient_pc_microphone);
    }).catch(() => {});
    fetch(`${getApiBase()}/api/sentinel/wfh`).then(r => r.json()).then(d => setWfhToday(!!d.wfh_today)).catch(() => {});
    const refresh = () => fetch(`${getApiBase()}/api/ambient-listening/status`)
      .then(r => r.json()).then(setAmbientStatus).catch(() => {});
    refresh();
    const timer = window.setInterval(refresh, 10000);
    fetch(`${getApiBase()}/api/ambient-listening/microphones`).then(r => r.json())
      .then(d => { setPcMicrophones(d.items || []); setPcMicrophone(d.selected || ''); }).catch(() => {});
    return () => window.clearInterval(timer);
  }, []);

  const setPcMic = async (name: string) => {
    setPcMicrophoneBusy(true);
    try {
      const r = await fetch(`${getApiBase()}/api/ambient-listening/microphone`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error('保存失败');
      const data = await r.json();
      setPcMicrophone(data.selected || '');
      showToast(name ? '电脑麦克风已选定，符合条件时自动切换' : '已恢复仅手机采集');
    } catch { showToast('❌ 电脑麦克风设置失败'); }
    finally { setPcMicrophoneBusy(false); }
  };

  const setAmbient = async (mode: 'off' | 'smart' | 'on') => {
    setAmbientBusy(true);
    try {
      const r = await fetch(`${getApiBase()}/api/ambient-listening/mode`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!r.ok) throw new Error('保存失败');
      const data = await r.json();
      setAmbientMode(mode); setAmbientStatus((old: any) => ({ ...(old || {}), capture: data.capture }));
      showToast(mode === 'off' ? '环境监听已关闭' : mode === 'on' ? '环境监听已打开（隐私规则仍优先）' : '环境监听已设为智能管理');
    } catch { showToast('❌ 环境监听设置失败'); }
    finally { setAmbientBusy(false); }
  };

  const saveAmbientThresholds = async () => {
    setAmbientThresholdBusy(true);
    try {
      const r = await fetch(`${getApiBase()}/api/ambient-listening/thresholds`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ambientThresholds),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail || '保存失败');
      setAmbientThresholds(data.thresholds);
      showToast('✅ 环境监听阈值已保存');
    } catch (e) {
      showToast(`❌ ${e instanceof Error ? e.message : '阈值保存失败'}`);
    } finally {
      setAmbientThresholdBusy(false);
    }
  };

  const toggleWfh = async () => {
    const next = !wfhToday;
    setWfhToday(next);
    try {
      await fetch(`${getApiBase()}/api/sentinel/wfh`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
      showToast(next ? '✅ 今日居家办公，暂不提醒去公司' : '已取消居家办公');
    } catch { setWfhToday(!next); showToast('❌ 设置失败'); }
  };

  // 城市列表点击外部关闭
  useEffect(() => {
    const h = (e: MouseEvent) => { if (cityBoxRef.current && !cityBoxRef.current.contains(e.target as Node)) setShowCityList(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const saveWeather = (patch: Partial<{ city: string; auto_refresh_location: boolean }>) => {
    saveSettings({ ...settings, weather: { city: weather.city, auto_refresh_location: weather.auto_refresh_location, ...patch } });
  };

  const pickCity = (name: string) => {
    setCityInput(name);
    setShowCityList(false);
    saveWeather({ city: name });
    showToast(`✅ 天气城市已设为 ${name}`);
  };

  const locateCity = async () => {
    setLocating(true);
    try {
      const r = await fetch(`${getApiBase()}/api/sentinel/weather/locate`, { method: 'POST' });
      const d = await r.json();
      if (d.success && d.city) {
        setCityInput(d.city);
        saveWeather({ city: d.city });
        showToast(`✅ 已定位到 ${d.city}`);
      } else {
        showToast(`❌ ${d.error || '定位失败'}`);
      }
    } catch {
      showToast('❌ 定位失败，请检查网络');
    }
    setLocating(false);
  };

  const saveSchedule = async () => {
    try {
      await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'user_schedule', value: schedule }),
      });
      showToast('✅ 作息已保存');
    } catch { showToast('❌ 保存失败'); }
  };

  const cityResults = searchCities(cityInput, 30);

  return (
    <div>
      {/* ── 环境监听 ── */}
      <div style={section}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>🎧 环境监听</div>
        <div style={{ fontSize: '0.72em', color: '#888', marginBottom: 8 }}>
          设置持久化在主机。工作日工作时段、隐私时段和系统麦克风禁令始终优先；“打开”不会绕过这些保护。
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
          {([
            ['off', '关闭'], ['smart', '智能管理'], ['on', '打开'],
          ] as const).map(([key, label]) => <button key={key} disabled={ambientBusy}
            onClick={() => setAmbient(key)}
            style={{ ...btnSm, padding: '6px 4px', borderColor: ambientMode === key ? '#4a9eff' : '#888', color: ambientMode === key ? '#4a9eff' : 'inherit' }}>
            {ambientMode === key ? '● ' : ''}{label}
          </button>)}
        </div>
        <div style={{ fontSize: '0.74em', marginTop: 8, color: ambientStatus?.capture?.allowed ? '#2f9e44' : '#b7791f' }}>
          主机判断：{ambientStatus?.capture?.allowed ? '允许采集' : '当前不采集'}
          {ambientStatus?.capture?.reason ? ` · ${ambientStatus.capture.reason}` : ''}
        </div>
        <div style={{ fontSize: '0.7em', color: '#888', marginTop: 4 }}>
          智能管理：away / active 立即采集；rest 达到下方未回复门槛后采集。电脑和手机共用三档及隐私规则；手机来源断线只在应用私有目录暂存，主机收妥或超过 24 小时即删除。
        </div>
        <div style={{ marginTop: 9, paddingTop: 8, borderTop: '1px solid rgba(0,0,0,0.08)' }}>
          <label style={lbl}>电脑优先麦克风（留空则仅手机采集）</label>
          <select style={inp} value={pcMicrophone} disabled={pcMicrophoneBusy}
            onChange={e => setPcMic(e.target.value)}>
            <option value="">仅手机</option>
            {pcMicrophone && !pcMicrophones.some(mic => mic.name === pcMicrophone)
              && <option value={pcMicrophone}>上次设备（当前不可用）</option>}
            {pcMicrophones.map(mic => <option key={mic.name} value={mic.name}>{mic.name}</option>)}
          </select>
          <div style={{ fontSize: '0.72em', marginTop: 5 }}>
            当前来源：{ambientStatus?.source?.selected === 'pc' ? '电脑麦克风'
              : ambientStatus?.source?.selected === 'paused' ? '已暂停' : '手机'}
            {ambientStatus?.source?.reason ? ` · ${sourceReasonLabels[ambientStatus.source.reason] || ambientStatus.source.reason}` : ''}
          </div>
          <div style={{ fontSize: '0.68em', color: '#888', marginTop: 3 }}>
            自动切换只把近期本机物理键鼠活动当作电脑在场线索；UU 等远程输入不算。电脑麦克风失效或离开电脑时回到手机。只采麦克风、不采系统回环；外放仍可能被麦克风听到。
          </div>
        </div>
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(0,0,0,0.08)' }}>
          <div style={{ fontSize: '0.78em', fontWeight: 600, marginBottom: 6 }}>参与与材料阈值</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6 }}>
            {([
              ['idle_minutes', '未回复门槛（分钟）', 1, 240],
              ['immediate_chars', '充足材料（字符）', 20, 10000],
              ['timed_min_chars', '时间兜底最少字符', 1, 10000],
              ['timed_seconds', '时间兜底等待（秒）', 10, 3600],
              ['cooldown_seconds', '评估冷却（秒）', 10, 3600],
            ] as const).map(([key, label, min, max]) => <div key={key}>
              <label style={lbl}>{label}</label>
              <input type="number" min={min} max={max} style={inp}
                value={ambientThresholds[key]}
                onChange={e => setAmbientThresholds(old => ({ ...old, [key]: Number(e.target.value) }))} />
            </div>)}
          </div>
          <button style={{ ...btnSm, marginTop: 7 }} disabled={ambientThresholdBusy} onClick={saveAmbientThresholds}>
            {ambientThresholdBusy ? '保存中…' : '保存监听阈值'}
          </button>
          <div style={{ fontSize: '0.68em', color: '#888', marginTop: 4 }}>
            评估条件保持原版 OR：达到“充足材料”，或者达到“最少字符”且等待到时；门槛只控制评估资格，不保证发送消息。
          </div>
        </div>
        {ambientStatus?.voice_identity?.available === false && <div style={{ fontSize: '0.7em', color: '#b7791f', marginTop: 4 }}>
          说话人身份：本地声纹模型未就绪，当前保留为“未知”，不会用音高猜人。
        </div>}
        {ambientStatus?.voice_identity?.available === true && <div style={{ fontSize: '0.7em', color: '#888', marginTop: 4 }}>
          本地声纹模型已就绪，已登记 {ambientStatus.voice_identity.samples?.length || 0} 份样本。请在“认知书 → 他者书 → 声纹录入”登记；识别结果仅为候选，混合讲话尚不逐句分离。
        </div>}
      </div>

      {/* ── 天气监控城市 ── */}
      <div style={section}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>🌤️ 天气监控城市</div>
        <div ref={cityBoxRef} style={{ position: 'relative' }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              type="text" style={inp} placeholder="搜索城市（可精确到区，如 余杭区,杭州）"
              value={cityInput}
              onChange={e => { setCityInput(e.target.value); setShowCityList(true); }}
              onFocus={() => setShowCityList(true)}
            />
            <button onClick={locateCity} disabled={locating} style={{ ...btnSm, whiteSpace: 'nowrap' }}>
              {locating ? '定位中…' : '📍 定位'}
            </button>
          </div>
          {showCityList && (
            <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 20, maxHeight: 200, overflowY: 'auto', background: 'var(--bg-card, #fff)', border: '1px solid #666', borderRadius: 4, marginTop: 2 }}>
              {cityResults.length === 0 ? (
                <div style={{ padding: '6px 10px', fontSize: '0.85em', color: '#888' }}>无匹配城市（可直接输入后失焦保存）</div>
              ) : cityResults.map(c => (
                <div key={c.province + c.name} onClick={() => pickCity(c.name)}
                  style={{ padding: '5px 10px', fontSize: '0.85em', cursor: 'pointer', display: 'flex', justifyContent: 'space-between' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'rgba(0,0,0,0.06)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <span>{c.name}</span><span style={{ color: '#888' }}>{c.province}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{ fontSize: '0.75em', color: '#888', marginTop: 4 }}>
          当前：{weather.city ? <b>{weather.city}</b> : <span style={{ color: '#f59e0b' }}>未设置（天气采集已暂停）</span>}
        </div>
        <div onClick={() => saveWeather({ auto_refresh_location: !weather.auto_refresh_location })}
          style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, cursor: 'pointer' }}>
          <span style={{ fontSize: '1.1em' }}>{weather.auto_refresh_location ? '☑️' : '⬜'}</span>
          <div>
            <div style={{ fontSize: '0.85em' }}>自动刷新位置</div>
            <div style={{ fontSize: '0.72em', color: '#888' }}>每次启动前端时自动定位并更新城市（需高德 Key + 定位权限）</div>
          </div>
        </div>
      </div>

      {/* ── 家 / 公司坐标 ── */}
      <div style={section}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>🏠 家 / 公司坐标</div>
        <div style={{ fontSize: '0.72em', color: '#888', marginBottom: 6 }}>家/公司地点范围匹配；在家/外出状态由家庭WiFi确认。其他室内落脚处可在下方登记。高德 Key 在后端 .env 配置。</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
          {(['home_lat', 'home_lng', 'work_lat', 'work_lng'] as const).map(k => (
            <div key={k}>
              <label style={lbl}>{{ home_lat: '家纬度', home_lng: '家经度', work_lat: '公司纬度', work_lng: '公司经度' }[k]}</label>
              <input type="number" step="0.0001" style={inp} value={(settings.gps as any)[k] ?? ''}
                onChange={e => saveSettings({ ...settings, gps: { ...settings.gps, [k]: parseFloat(e.target.value) || null } })} />
            </div>
          ))}
          <div>
            <label style={lbl}>家半径(m)</label>
            <input type="number" style={inp} value={settings.gps.home_radius_m}
              onChange={e => saveSettings({ ...settings, gps: { ...settings.gps, home_radius_m: parseInt(e.target.value) || 200 } })} />
          </div>
          <div>
            <label style={lbl}>公司半径(m)</label>
            <input type="number" style={inp} value={settings.gps.work_radius_m}
              onChange={e => saveSettings({ ...settings, gps: { ...settings.gps, work_radius_m: parseInt(e.target.value) || 200 } })} />
          </div>
        </div>
      </div>

      <GpsPlaceSettings places={settings.gps.places || []} showToast={showToast}
        onSave={async places => (await saveSettings({ ...settings, gps: { ...settings.gps, places } })) === true} />

      {/* ── 作息时间 ── */}
      <div style={section}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>📅 作息时间</div>
        <div style={{ fontSize: '0.72em', color: '#888', marginBottom: 6 }}>用于"工作时间在家"等提醒。留空则相关规则不生效。</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {(['work_start', 'work_end', 'lunch_start', 'lunch_end'] as const).map(f => (
            <div key={f}>
              <label style={lbl}>{{ work_start: '上班', work_end: '下班', lunch_start: '午休开始', lunch_end: '午休结束' }[f]}</label>
              <input type="time" style={inp} value={schedule[f]} onChange={e => setSchedule(p => ({ ...p, [f]: e.target.value }))} />
            </div>
          ))}
        </div>
        <button onClick={saveSchedule} style={{ marginTop: 10, padding: '4px 16px', borderRadius: 4, border: '1px solid #4a9eff', background: 'transparent', color: '#4a9eff', cursor: 'pointer' }}>保存作息</button>
        <div onClick={toggleWfh} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, cursor: 'pointer', paddingTop: 10, borderTop: '1px dashed rgba(0,0,0,0.12)' }}>
          <span style={{ fontSize: '1.1em' }}>{wfhToday ? '☑️' : '⬜'}</span>
          <div>
            <div style={{ fontSize: '0.85em' }}>今天居家办公</div>
            <div style={{ fontSize: '0.72em', color: '#888' }}>勾选后当天不提醒"工作时间还在家"，次日自动恢复</div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default PerceptionSettingsTab;
