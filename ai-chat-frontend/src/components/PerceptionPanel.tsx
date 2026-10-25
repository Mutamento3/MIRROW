import React, { useState, useEffect } from 'react';
import { getApiBase, getPlatform } from '../config';
import { useSentinelStatus, usePerceptionSettings, useSentinelToggle, useSentinelAutoRefresh, useHrDevices } from '../hooks/useSentinel';
import BaselineDashboard from './BaselineDashboard';
import DeviationTimeline from './DeviationTimeline';
import PerceptionSettingsTab from './PerceptionSettingsTab';
import MiBandPanel from './MiBandPanel';

interface Props { show: boolean; onClose: () => void; onTriggerSleepPrompt?: () => void; }
type TabId = 'overview'|'health'|'records'|'thresholds'|'baseline'|'deviations'|'triggers'|'settings';
const TABS:{id:TabId;label:string}[]=[{id:'overview',label:'📊 总览'},{id:'health',label:'📥 采集'},{id:'baseline',label:'📈 基线'},{id:'deviations',label:'⚠️ 偏离'},{id:'triggers',label:'🔔 触发'},{id:'records',label:'📋 记录'},{id:'thresholds',label:'⚙️ 阈值'},{id:'settings',label:'🛠️ 设置'}];
const PC:Record<string,string>={critical:'#ef4444',high:'#f59e0b',medium:'#3b82f6',low:'#9ca3af'};
const SD:Record<string,string>={heart_rate:'BLE 手环心率。5分钟自动重连。设备在采集页扫描配对。',behavior_state:'合并 PC 前台进程 + 手机前台App + 键鼠活跃，推断你在用电脑/手机/双设备/离开。',input_idle:'Windows GetLastInputInfo API，检测键鼠空闲。始终可用。',mirrow_visibility:'MIRROW 页面是否在前台且有心跳。',user_status:'用户声明的当前状态（外出/睡眠/游戏等）。',period:'生理期跟踪，从日历系统读取。',step_count:'手机步数传感器 relay，每5分钟轮询。',gps_location:'手机 GPS + 高德逆地理编码，每10分钟轮询。',screen_time:'手机屏幕使用时长（UsageStatsManager），每10分钟上报。',weather:'wttr.in 天气，一天一更新。weatherCode+云量/紫外线/风速/降水/能见度自建中文描述。城市在🛠️设置里配。'};
const TR:Record<string,{desc:string;level:string;icon:string}>={
  hr_surge:{desc:'心率骤升（+20bpm / 5min）——瞬间情绪波动或运动开始',level:'L1',icon:'📈'},
  hr_drop:{desc:'心率骤降（-20bpm / 5min）——突然放松或传感器脱落',level:'L1',icon:'📉'},
  hr_sustained_high:{desc:'持续心率偏高（>105 bpm × 连续3次）——紧张/运动/身体不适',level:'L1',icon:'💓'},
  steps_burst:{desc:'步数暴增（+500步/tick）——突然开始走动',level:'L1',icon:'🏃'},
  steps_stagnant:{desc:'步数停滞（≥30min未变化）——久坐或传感器断开',level:'L1',icon:'🪑'},
  gps_category_change:{desc:'位置分类切换（家⇄公司⇄其他）——通勤或外出',level:'L1',icon:'📍'},
  gps_moved:{desc:'显著移动（>500m）——正在路上',level:'L1',icon:'🚗'},
  behavior_state_change:{desc:'设备类别切换（PC⇄手机⇄双设备⇄离开）——注意力转移',level:'L1',icon:'🔄'},
  app_session_long:{desc:'单App连续使用>3小时——可能是久坐或沉浸工作',level:'L1',icon:'💻'},
  status_change_silent:{desc:'用户状态变更+沉默>3min——状态切换后未说话',level:'L1',icon:'🤫'},
  C1:{desc:'心率>120 + 静息状态（键鼠空闲>5min）——静息时异常高心率，立即推送',level:'L2',icon:'🚨'},
  C2:{desc:'Δ心率>40（2min窗口）+ 非外出——心率剧烈波动，立即推送',level:'L2',icon:'🚨'},
};
const isMobile=getPlatform()==='mobile';
const SI=(name:string,st:Record<string,string>|undefined)=>{const s=st?.[name];return s==='connected'?'🟢':s==='failed'?'🔴':s==='unavailable'?'⚪':'⚪';};
const btnSm:React.CSSProperties={fontSize:'0.75em',padding:'2px 8px',borderRadius:3,border:'1px solid #888',cursor:'pointer',background:'transparent',color:'inherit'};
const lbl:React.CSSProperties={fontSize:'0.8em',color:'#888',display:'block',marginBottom:2};
const inp:React.CSSProperties={width:'100%',padding:'3px 6px',borderRadius:3,border:'1px solid #666',background:'transparent',color:'inherit',fontSize:'0.85em'};
const fmtAgo=(iso:string):string=>{try{const t=new Date(iso.slice(0,19)).getTime();const diff=Date.now()-t;const m=Math.floor(diff/60000);if(m<1)return'刚刚';if(m<60)return`${m}分钟前`;const h=Math.floor(m/60);if(h<24)return`${h}小时前`;return`${Math.floor(h/24)}天前`;}catch{return'';}};

const MetricCard:React.FC<{label:string;icon:string;status:string|null;expanded:boolean;onToggle:()=>void;actions?:React.ReactNode;children?:React.ReactNode}>=({label,icon,status,expanded,onToggle,actions,children})=>(<div style={{padding:'8px 12px',borderRadius:8,background:'rgba(0,0,0,0.03)',marginBottom:8,border:'1px solid rgba(0,0,0,0.06)'}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center',userSelect:'none'}}><span onClick={onToggle} style={{fontWeight:600,cursor:'pointer',flex:1,padding:'4px 0'}}>{icon} {label} {expanded?'▾':'▸'}</span><div style={{display:'flex',gap:6,alignItems:'center'}} onClick={e=>e.stopPropagation()}>{status&&<span style={{fontSize:'0.85em'}}>{status}</span>}{actions}</div></div>{expanded&&<div style={{marginTop:8,paddingTop:8,borderTop:'1px solid rgba(0,0,0,0.08)'}}>{children}</div>}</div>);

export const PerceptionPanel:React.FC<Props>=({show,onClose,onTriggerSleepPrompt})=>{
const[tab,setTab]=useState<TabId>('overview');const{status,refetch}=useSentinelStatus();const{settings,saveSettings}=usePerceptionSettings();const{reconnectHr}=useSentinelToggle();
const[st,setSt]=useState({today:0,outing:0,avg7d:0});const[baseline,setBaseline]=useState<any>(null);const[sleepData,setSleepData]=useState<any>(null);const[healthExp,setHealthExp]=useState<Record<string,boolean>>({hr:false,steps:false,sleep:false,gps:false});
const[recDate,setRecDate]=useState(new Date().toISOString().slice(0,10));const[recEvents,setRecEvents]=useState<any[]>([]);const[recOffset,setRecOffset]=useState(0);const[recTotal,setRecTotal]=useState(0);const[recLoading,setRecLoading]=useState(false);
const[expandedSrc,setExpandedSrc]=useState<string|null>(null);
const{scanPc,scanMobile}=useHrDevices();const[hrDevices,setHrDevices]=useState<any[]>([]);const[hrScanning,setHrScanning]=useState(false);const[showHrGuide,setShowHrGuide]=useState(false);
const scanHr=async(which:'pc'|'mobile')=>{if(hrDevices.length>0){setHrDevices([]);return;}setHrScanning(true);setHrDevices([]);try{const d=which==='pc'?await scanPc():await scanMobile();if(d.success){setHrDevices(d.devices||[]);if(!(d.devices||[]).length)showToast('未扫描到设备（确认蓝牙已开+已配对）');}else showToast(`❌ ${d.error||'扫描失败'}`);}catch{showToast('❌ 扫描失败');}setHrScanning(false);};
const selectHr=(dev:any)=>{saveSettings({...settings,hr_device:{mac:dev.address,name:dev.name||''}});showToast(`✅ 已选: ${dev.name||dev.address}`);};

useEffect(()=>{if(!show)return;refetch();fetchHealth();fetchBaseline();fetchRecords(recDate,0);},[show]);
const fetchHealth=async()=>{try{const today=new Date().toISOString().slice(0,10);const weekAgo=new Date(Date.now()-30*86400000).toISOString().slice(0,10);const[sr,slr]=await Promise.all([fetch(`${getApiBase()}/api/health/daily?from_date=${weekAgo}&to_date=${today}`),fetch(`${getApiBase()}/api/health/cache/sleep`)]);const sd=await sr.json();if(sd.data?.length>0){const todayRow=sd.data.find((d:any)=>d.date===today);const stepsVals=sd.data.filter((d:any)=>(d.steps||0)>0).map((d:any)=>d.steps);const avg7d=stepsVals.length>0?Math.round(stepsVals.reduce((a:number,b:number)=>a+b,0)/Math.min(stepsVals.length,7)):0;setSt({today:todayRow?.steps||0,outing:todayRow?.outing_steps||0,avg7d});}const sl=await slr.json();if(sl.data)setSleepData(sl);}catch{}};
const[toastMsg,setToastMsg]=useState('');const showToast=(m:string,dur=3000)=>{setToastMsg(m);setTimeout(()=>setToastMsg(''),dur);};const fetchBaseline=async()=>{try{const r=await fetch(`${getApiBase()}/api/health/baseline`);setBaseline(await r.json());}catch{}};
const[triggers,setTriggers]=useState<any>(null);const fetchTriggers=async()=>{try{const r=await fetch(`${getApiBase()}/api/sentinel/triggers`);if(!r.ok)return;const d=await r.json();if(d?.l1)setTriggers(d);}catch{}};
useEffect(()=>{if(!show||tab!=='triggers')return;fetchTriggers();const i=setInterval(fetchTriggers,10000);return()=>clearInterval(i);},[show,tab]);
const[deviationData,setDeviationData]=useState<any>(null);const fetchDeviations=async()=>{try{const r=await fetch(`${getApiBase()}/api/health/baseline/deviations?days=7`);setDeviationData(await r.json());}catch{}};
useSentinelAutoRefresh(show,refetch);
const refreshSteps=async()=>{try{const r=await fetch(`${getApiBase()}/api/sentinel/refresh`,{method:'POST'});const d=await r.json();if(d.success){showToast(`✅ 快照已刷新: ${d.snapshot?.steps_today??'—'}步`);fetchHealth();refetch();}else{showToast(`❌ ${d.error||'刷新失败'}`);}}catch{showToast('❌ 刷新失败，请确认后端运行中');}};
const fetchRecords=async(date:string,offset:number)=>{setRecLoading(true);try{const res=await fetch(`${getApiBase()}/api/sentinel/events?date=${date}&limit=50&offset=${offset}`);const data=await res.json();if(offset===0){setRecEvents(data.events||[]);}else{setRecEvents(p=>[...p,...(data.events||[])]);}setRecOffset(offset+(data.events||[]).length);setRecTotal(data.total||0);}catch{}setRecLoading(false);};
const l3IntervalMin=triggers?.l3?.interval_min;
const l3Description=typeof l3IntervalMin==='number'&&Number.isFinite(l3IntervalMin)&&l3IntervalMin>0
  ?`距上次 Flash 触发达到 ${l3IntervalMin} 分钟时，进行保底检查；间隔随当前状态调整。`
  :'当前保底检查间隔暂不可用。';
if(!show)return null;

return(<div className="ledger-overlay" onClick={onClose}><div className="ledger-modal perception-panel" style={{maxWidth:560,maxHeight:'85vh'}} onClick={e=>e.stopPropagation()}>
<div className="ledger-title"><span>🛡️ 哨兵监控</span><button className="ledger-close-btn" onClick={onClose}>✕</button></div>
{toastMsg&&<div style={{padding:'4px 12px',marginBottom:4,borderRadius:4,background:toastMsg.startsWith('✅')?'rgba(34,197,94,0.1)':'rgba(239,68,68,0.1)',fontSize:'0.85em'}}>{toastMsg}</div>}
<div className="monitor-tabs perception-tabs" style={{marginBottom:8}}>{TABS.map(t=><button key={t.id} className={`monitor-tab${tab===t.id?' active':''}`} onClick={()=>{setTab(t.id);if(t.id==='records')fetchRecords(recDate,0);if(t.id==='health')fetchHealth();if(t.id==='baseline')fetchBaseline();if(t.id==='deviations')fetchDeviations();if(t.id==='triggers')fetchTriggers();}}>{t.label}</button>)}</div>
<div style={{maxHeight:'60vh',overflowY:'auto'}}>

{/* ═══ 📊 总览 ═══ */}
{tab==='overview'&&status&&(<div>
<div style={{padding:'6px 10px',borderRadius:6,background:'rgba(0,0,0,0.03)',marginBottom:6,display:'flex',justifyContent:'space-between'}}><span>运行: {status.running?'✅':'⏸️'} · tick #{status.tick_count} · {status.tick_interval}s</span><span>告警: {status.recent_alerts?.length||0}</span></div>
<h4 style={{margin:'8px 0 4px'}}>数据源（点击查看说明）</h4>
{(()=>{const raw=status.sources||[];const seen=new Set<string>();const list:string[]=[];for(const n of raw){if(n==='screen_activity'||n==='current_app'){if(!seen.has('behavior_state')){seen.add('behavior_state');list.push('behavior_state');}}else{list.push(n);}}return list;})().map((name:string)=>{
const mergedState=()=>{const rank=(s?:string)=>s==='connected'?3:s==='disconnected'?2:s==='failed'?1:0;const sa=status.source_states?.['screen_activity'];const ca=status.source_states?.['current_app'];return rank(sa)>=rank(ca)?(sa||ca||'unknown'):(ca||'unknown');};
const st=name==='behavior_state'?mergedState():(status.source_states?.[name]||'unknown');const err=name==='behavior_state'?(status.source_errors?.['screen_activity']||status.source_errors?.['current_app']):status.source_errors?.[name];const ex=expandedSrc===name;return(<div key={name}><div onClick={()=>setExpandedSrc(ex?null:name)} style={{display:'flex',justifyContent:'space-between',padding:'3px 8px',fontSize:'0.85em',cursor:'pointer',borderRadius:4,background:ex?'rgba(0,0,0,0.04)':'transparent'}}><span>{SI(name==='behavior_state'?'screen_activity':name,status.source_states)} {name}</span><span style={{color:st==='connected'?'#22c55e':st==='failed'||st==='unavailable'?'#ef4444':'#888',textAlign:'right'}}>{st}{err&&(st==='failed'||st==='disconnected')?<div style={{fontSize:'0.85em',color:'#f59e0b',fontWeight:400}}>{err.slice(0,24)}</div>:null}</span></div>{ex&&<div style={{fontSize:'0.78em',color:'#888',padding:'2px 8px 6px 24px'}}>{(()=>{const sn=status.last_snapshot;if(!sn)return SD[name];switch(name){case'heart_rate':return<>{status.hr_last_reading!=null?<div style={{color:'#333',marginBottom:2}}>📡 最近: {Math.round(status.hr_last_reading)} bpm{status.hr_last_reading_time?` · ${fmtAgo(status.hr_last_reading_time)}`:''}</div>:<div style={{color:'#888'}}>📡 暂无读数</div>}{SD[name]}</>;case'input_idle':return<>{sn.input_idle_seconds!=null?<div style={{color:'#333',marginBottom:2}}>📡 {sn.input_idle_seconds<120?`${sn.input_idle_seconds.toFixed(0)}s 空闲`:`${(sn.input_idle_seconds/60).toFixed(0)}min 空闲`}</div>:null}{SD[name]}</>;case'behavior_state':return<>{sn.behavior_state?<div style={{color:'#4a9eff',marginBottom:2,fontWeight:600}}>🧠 {sn.behavior_state}</div>:null}{sn.screen_active!=null?<div style={{color:'#333',marginBottom:2}}>🖥️ PC: {sn.screen_active?'活跃中':'静止'}{sn.foreground_app?` · ${sn.foreground_app.slice(0,24)}`:''}</div>:null}{sn.mobile_app_package?<div style={{color:'#333',marginBottom:2}}>📱 手机: {sn.mobile_app_name||sn.mobile_app_package}{sn.mobile_screen_on?' · 🔆亮屏':''}</div>:(sn.screen_active==null?<div style={{color:'#888'}}>📡 暂无读数</div>:null)}{SD[name]}</>;case'mirrow_visibility':return<>{sn.mirrow_visible!=null?<div style={{color:'#333',marginBottom:2}}>📡 {sn.mirrow_visible?'✅ 前台可见':'❌ 后台/不可见'}</div>:null}{SD[name]}</>;case'user_status':return<>{sn.user_status?<div style={{color:'#333',marginBottom:2}}>📡 当前: {sn.user_status}</div>:null}{SD[name]}</>;case'period':return<>{sn.is_period!=null?<div style={{color:'#333',marginBottom:2}}>📡 {sn.is_period?`生理期第${sn.period_day||'?'}天`:'非生理期'}</div>:null}{SD[name]}</>;case'step_count':return<>{sn.steps_today!=null?<div style={{color:'#333',marginBottom:2}}>📡 今日: {sn.steps_today} 步{sn.steps_stagnant_minutes>0?` · 停滞${sn.steps_stagnant_minutes}min`:''}</div>:<div style={{color:'#888'}}>📡 暂无读数</div>}{SD[name]}</>;case'gps_location':return<>{sn.location_address?<div style={{color:'#333',marginBottom:2}}>📡 {sn.location_address.slice(0,30)}{sn.location_category?` (${sn.location_category})`:''}</div>:<div style={{color:'#888'}}>📡 暂无定位</div>}{SD[name]}</>;case'screen_time':return<>{sn.screen_time_minutes!=null?<div style={{color:'#333',marginBottom:2}}>📡 今日: {sn.screen_time_minutes}min{sn.top_app_category?` · 主要: ${sn.top_app_category}`:''}</div>:<div style={{color:'#888'}}>📡 暂无读数（需手机 relay）</div>}{SD[name]}</>;case'weather':{const wd=sn.weather_desc;const details:string[]=[];if(sn.weather_wind_speed!=null)details.push(`🌬️ ${sn.weather_wind_dir||''} ${sn.weather_wind_speed}km/h`);if(sn.weather_uv_index!=null)details.push(`☀️ UV${sn.weather_uv_index}`);if(sn.weather_cloudcover!=null)details.push(`☁️ 云量${sn.weather_cloudcover}%`);if(sn.weather_precip_mm!=null&&sn.weather_precip_mm>0)details.push(`🌧️ ${sn.weather_precip_mm}mm`);if(sn.weather_visibility!=null)details.push(`👁️ 能见度${sn.weather_visibility}km`);return<>{wd?<><div style={{color:'#333',marginBottom:2}}>📡 {wd}{sn.weather_temp!=null?` ${sn.weather_temp}°C`:''}{sn.weather_humidity!=null?` · 湿度${sn.weather_humidity}%`:''}</div>{details.length>0?<div style={{fontSize:'0.92em',color:'#666',marginBottom:2}}>{details.join(' · ')}</div>:null}</>:<div style={{color:'#888'}}>📡 暂无天气（未配置城市或今日未采集）</div>}{SD[name]}</>;}default:return SD[name];}})()}</div>}</div>);})}
<div onClick={()=>setTab('triggers')} style={{padding:'8px 12px',borderRadius:6,background:'rgba(59,130,246,0.06)',cursor:'pointer',textAlign:'center',fontSize:'0.85em',marginTop:8}}>🔔 查看规则与触发器 →</div>
</div>)}

{/* ═══ ❤️ 健康 ═══ */}
{tab==='health'&&(<div>
<MiBandPanel />
<MetricCard label="心率" icon="" status={(status?.hr_connection||'—')} expanded={healthExp.hr} onToggle={()=>setHealthExp(p=>({...p,hr:!p.hr}))} actions={<button onClick={async()=>{const result=await reconnectHr();showToast(result?.success?'心率已刷新':`刷新失败: ${result?.error||'未知错误'}`);setTimeout(()=>{refetch();},3000);}} style={btnSm}>🔄 刷新</button>}>
{status?.hr_last_reading!=null?<div><span style={{fontSize:'1.3em',fontWeight:700,color:status.hr_last_reading>100?'#ef4444':status.hr_last_reading>85?'#f59e0b':'#10b981'}}>❤️ {Math.round(status.hr_last_reading)}</span><span style={{fontSize:'0.75em',color:'#888',marginLeft:4}}>bpm</span>{status.hr_last_reading_time?<span style={{fontSize:'0.8em',color:'#888',marginLeft:8}}>· 采集于 {fmtAgo(status.hr_last_reading_time)}</span>:''}</div>:<div style={{color:'#888'}}>暂无读数（PC 蓝牙未连手环时，由手机每 5 分钟接管采集）</div>}
<div style={{marginTop:10,fontSize:'0.85em',borderTop:'1px dashed rgba(0,0,0,0.12)',paddingTop:8}}>
<div style={{marginBottom:4}}>设备: {settings.hr_device?.mac?<b>{settings.hr_device.name||'(无名)'} · {settings.hr_device.mac}</b>:<span style={{color:'#888'}}>未选择，请扫描配对</span>}</div>
<div style={{display:'flex',gap:6,marginBottom:6,flexWrap:'wrap'}}>
{isMobile?<button style={btnSm} disabled={hrScanning} onClick={()=>scanHr('mobile')}>📱 手机已配对</button>:<button style={btnSm} disabled={hrScanning} onClick={()=>scanHr('pc')}>💻 PC 扫描</button>}
<button style={btnSm} onClick={async()=>{const r=await reconnectHr();showToast(r?.success?`✅ 连接成功${r.heart_rate?` ${r.heart_rate}bpm`:''}`:`❌ ${r?.error||'失败'}`,8000);setTimeout(refetch,2000);}}>🔗 测试连接</button>
</div>
{hrScanning&&<div style={{color:'#888',marginBottom:4}}>扫描中…（约 6s）</div>}
{hrDevices.map((d:any)=>(<div key={(d.source||'')+d.address} onClick={()=>selectHr(d)} style={{padding:'4px 8px',borderRadius:4,marginBottom:2,cursor:'pointer',background:settings.hr_device?.mac===d.address?'rgba(34,197,94,0.12)':'rgba(0,0,0,0.03)',display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}><span>{d.has_hr_service?'❤️ ':''}{d.name||'(无名设备)'}</span><span style={{color:'#888',fontFamily:'monospace',fontSize:'0.85em'}}>{d.address}{d.rssi!=null?` · ${d.rssi}dBm`:''}</span></div>))}
<div onClick={()=>setShowHrGuide(!showHrGuide)} style={{fontSize:'0.8em',color:'#888',cursor:'pointer',marginTop:6}}>❓ 配对引导 {showHrGuide?'▲':'▼'}</div>
{showHrGuide&&<ol style={{fontSize:'0.8em',color:'#666',paddingLeft:18,marginTop:4,lineHeight:1.7}}><li>手环开心率广播：华为→「华为运动健康」App → 设备 → 心率 → 开启「心率广播」（其他品牌找类似"心率广播/Heart Rate Broadcast"）</li><li>系统蓝牙先配对手环：电脑（用 PC 扫描）或手机（用手机已配对）的系统蓝牙设置里配对一次——配对后地址稳定，扫描才能拿到长期可用的 identity address</li><li>回来点「💻 PC 扫描」或「📱 手机已配对」，从列表选中手环，再点「🔗 测试连接」确认读到 bpm</li></ol>}
</div>
</MetricCard>
<MetricCard label="步数" icon="" status={(status?.source_states?.step_count||'—')} expanded={healthExp.steps} onToggle={()=>{setHealthExp(p=>({...p,steps:!p.steps}));fetchHealth();fetchBaseline();}} actions={<button onClick={refreshSteps} style={btnSm}>🔄 刷新</button>}>
{st.today>0||(status?.source_states?.step_count==='connected')?<><div style={{fontSize:'1.1em',fontWeight:700}}>{st.today} <span style={{fontSize:'0.7em',color:'#888',fontWeight:400}}>步</span></div><div style={{fontSize:'0.85em',color:'#888',marginTop:2}}>外出 {st.outing} 步 · 7日均值 {st.avg7d} 步{(()=>{const sd=(settings as any)?._weekly_steps_delta;if(sd>0)return<span style={{color:'#22c55e'}}> ↑{sd}%</span>;if(sd<0)return<span style={{color:'#ef4444'}}> ↓{Math.abs(sd)}%</span>;return null;})()}</div>{baseline?.dimensions?.steps?.available&&st.today>0?<div style={{fontSize:'0.85em',marginTop:2}}>{(()=>{const bs=baseline.dimensions.steps;const bMean=parseFloat(bs.summary);const bm=isNaN(bMean)?0:bMean;const ratio=bm>0?Math.round(st.today/bm*100):100;const color=ratio<30?'#ef4444':ratio<60?'#f59e0b':ratio>150?'#3b82f6':'#22c55e';const label=ratio<30?'严重偏低':ratio<60?'偏低':ratio>150?'偏高':'正常';return<span>基准: <span style={{fontWeight:600,color}}>{bs.summary}</span> · <span style={{color}}>{ratio}% ({label})</span></span>;})()}{baseline?.trend_signals?.steps?<span style={{display:'block',fontSize:'0.8em',color:'#f59e0b',marginTop:1}}>📉 {baseline.trend_signals.steps}</span>:null}</div>:null}{baseline&&!baseline.dimensions?.steps?.available&&<div style={{fontSize:'0.75em',color:'#888',marginTop:2}}>🧪 基准学习中（需更多天数数据）</div>}<div style={{fontSize:'0.75em',color:'#888',marginTop:4}}>💡 每5分钟自动轮询 · 手动刷新即时采集</div></>:<div style={{color:'#888'}}>手机未连接，步数暂不可用</div>}
</MetricCard>
<MetricCard label="睡眠" icon="" status={null} expanded={healthExp.sleep} onToggle={()=>setHealthExp(p=>({...p,sleep:!p.sleep}))} actions={<button onClick={()=>onTriggerSleepPrompt?.()} style={btnSm}>🔄 采集</button>}>
{sleepData?.data?<div style={{fontSize:'1.1em',fontWeight:700}}>{typeof sleepData.data==='object'?(()=>{const d=sleepData.data;const dur=d.total_sleep_min||d.sleep_min||d.sleep_minutes;const ts=d.captured_at||d.ts;return<><span style={{fontWeight:700}}>{dur?`睡眠 ${Math.floor(dur/60)}h${dur%60}min`:'时长未知'}</span>{ts?<span style={{fontSize:'0.8em',color:'#888',marginLeft:8}}>采集于 {new Date(ts).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span>:''}</>;})():String(sleepData.data)}</div>:<div style={{color:'#888'}}>暂无睡眠数据<br/>💡 从「睡眠/小憩」切换状态时自动采集</div>}
</MetricCard>
<MetricCard label="位置" icon="" status={(status?.source_states?.gps_location||'—')} expanded={healthExp.gps} onToggle={()=>setHealthExp(p=>({...p,gps:!p.gps}))} actions={<button onClick={async()=>{try{const r=await fetch(`${getApiBase()}/api/sentinel/gps/test`,{method:'POST'});const d=await r.json();showToast(d.success?`✅ ${d.address||d.category||'已定位'}`:`❌ ${d.error||'GPS连接失败'}`);refetch();}catch{showToast('❌ GPS连接失败');}}} style={btnSm}>🔄 刷新</button>}>
{status?.last_snapshot?.location_address||status?.last_snapshot?.location_place_name?<><div style={{fontSize:'1.05em',fontWeight:700}}>📍 {status.last_snapshot.location_label||status.last_snapshot.location_address}</div>{status.last_snapshot.location_address?<div style={{fontSize:'0.8em',color:'#888',marginTop:4}}>{status.last_snapshot.location_address}</div>:null}<div style={{fontSize:'0.75em',color:'#888',marginTop:4}}>💡 每10分钟自动定位 · 手动刷新即时采集 · 家/公司及固定落脚处在🛠️设置里配</div></>:<div style={{color:'#888'}}>{status?.last_snapshot?.location_label||'暂无定位'}{status?.source_errors?.gps_location?<div style={{fontSize:'0.85em',color:'#f59e0b',marginTop:2}}>{status.source_errors.gps_location}</div>:<div style={{fontSize:'0.8em',marginTop:2}}>需手机连接 + 高德 API Key（后端 .env）</div>}</div>}
</MetricCard>
</div>)}

{/* ═══ 🛠️ 设置（天气城市 + 家/公司坐标 + 作息）═══ */}
{tab==='settings'&&(<PerceptionSettingsTab settings={settings} saveSettings={saveSettings} showToast={showToast}/>)}

{/* ═══ 📈 基线 ═══ */}
{tab==='baseline'&&(<div>
<BaselineDashboard baseline={baseline} todaySteps={st.today} todayHr={status?.last_snapshot?.heart_rate??null} />
</div>)}

{/* ═══ ⚠️ 偏离 ═══ */}
{tab==='deviations'&&(<div>
<DeviationTimeline data={deviationData} />
</div>)}

{/* ═══ 📋 记录 + ⚙️ 阈值 ═══ */}

{tab==='records'&&(<div><div style={{display:'flex',gap:8,marginBottom:8,alignItems:'center'}}><input type="date" style={inp} value={recDate} onChange={e=>{setRecDate(e.target.value);fetchRecords(e.target.value,0);}}/><span style={{fontSize:'0.85em',color:'#888'}}>{recEvents.length}/{recTotal} 条</span></div>{recLoading&&recEvents.length===0?<div style={{textAlign:'center',padding:20}}>加载中...</div>:recEvents.length===0?<div style={{color:'#888',textAlign:'center',padding:20}}>当日无记录</div>:<div style={{maxHeight:400,overflowY:'auto'}}>{recEvents.map((e:any,i:number)=>{const p=e.pushed===1;return(<div key={i} style={{padding:'4px 6px',borderLeft:`3px solid ${p?PC[e.priority]||'#888':'#888'}`,marginBottom:3,fontSize:'0.85em',opacity:p?1:0.7}}><span>{p?'📨':'🔇'}</span><span style={{color:PC[e.priority],fontWeight:600,marginLeft:2}}>{e.priority}</span><span style={{marginLeft:4}}>{e.message||e.rule_id}</span><span style={{float:'right',color:'#888',fontSize:'0.75em'}}>{e.timestamp?.slice(11,16)}</span></div>);})}{recEvents.length<recTotal&&<button onClick={()=>fetchRecords(recDate,recOffset)} disabled={recLoading} style={{...btnSm,display:'block',margin:'8px auto'}}>{recLoading?'加载中...':'加载更多'}</button>}</div>}</div>)}

{/* ═══ 🔔 触发器 ═══ */}
{tab==='triggers'&&(<div>{!triggers?<div style={{textAlign:'center',padding:20}}>加载中...</div>:
<div>
<div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:8,marginBottom:12,textAlign:'center'}}>
<div style={{padding:8,borderRadius:6,background:'rgba(59,130,246,0.08)'}}><div style={{fontSize:'1.5em',fontWeight:700,color:'#3b82f6'}}>{triggers.l1?.length||0}</div><div style={{fontSize:'0.75em',color:'#888'}}>L1 快照对比</div></div>
<div style={{padding:8,borderRadius:6,background:'rgba(239,68,68,0.08)'}}><div style={{fontSize:'1.5em',fontWeight:700,color:'#ef4444'}}>{triggers.l2?.length||0}</div><div style={{fontSize:'0.75em',color:'#888'}}>L2 救命规则</div></div>
<div style={{padding:8,borderRadius:6,background:'rgba(34,197,94,0.08)'}}><div style={{fontSize:'1.5em',fontWeight:700,color:'#22c55e'}}>{(triggers.l3?.next_seconds>0?Math.ceil(triggers.l3.next_seconds/60):0)}</div><div style={{fontSize:'0.75em',color:'#888'}}>L3 保底(分)</div></div></div>

{/* L1/L2/L3 规则列表 */}
<div style={{marginBottom:12}}>
<div style={{fontWeight:600,marginBottom:6,color:'#3b82f6'}}>🟦 L1 — 数据源快照对比（tick-to-tick 变化，跨阈值单次触发）</div>
{(triggers.l1||[]).flatMap((src:any)=>Array.isArray(src?.events)?src.events.map((ev:string)=>({ev,name:src.name})):[]).map(({ev,name}:{ev:string;name:string},i:number)=>{if(typeof ev!=='string')return null;const ti=TR[ev];return ti?<div key={ev+i} title={ti.desc} style={{fontSize:'0.82em',padding:'4px 8px',marginBottom:2,borderRadius:4,background:'rgba(59,130,246,0.04)',cursor:'default',display:'flex',alignItems:'center',gap:6}}><span>{ti.icon}</span><span style={{flex:1}}>{ti.desc}</span><span style={{fontSize:'0.75em',color:'#888',fontFamily:'monospace'}}>{ev}</span></div>:<div key={ev+i} style={{fontSize:'0.82em',padding:'4px 8px',color:'#888'}}>{name}: {ev}</div>;})}
<div style={{fontWeight:600,margin:'8px 0 6px',color:'#ef4444'}}>🟥 L2 — 救命规则（绕Flash直推，不等待分析）</div>
{(triggers.l2||[]).map((t:any,i:number)=>{const id=typeof t==='string'?t:t?.id;const ti=id?TR[id]:undefined;const desc=(typeof t==='object'&&t?.desc)||ti?.desc||(typeof t==='object'?t?.name:t)||'';return <div key={id||i} title={String(desc)} style={{fontSize:'0.82em',padding:'4px 8px',marginBottom:2,borderRadius:4,background:'rgba(239,68,68,0.04)',cursor:'default',display:'flex',alignItems:'center',gap:6}}><span>{ti?.icon||'🚨'}</span><span style={{flex:1}}>{String(desc)}</span><span style={{fontSize:'0.75em',color:'#888',fontFamily:'monospace'}}>{String(id||'')}</span></div>;})}
<div style={{fontWeight:600,margin:'8px 0 6px',color:'#22c55e'}}>🟩 L3 — 保底定时唤醒</div>
<div title={l3Description} style={{fontSize:'0.82em',padding:'4px 8px',borderRadius:4,background:'rgba(34,197,94,0.04)',display:'flex',alignItems:'center',gap:6}}><span>⏰</span><span>{l3Description}</span></div>

{/* anomaly_detector 规则（阈值直推 + 仅记录）*/}
{(triggers as any).rules_instant?.length>0&&<>
<div style={{fontWeight:600,margin:'8px 0 6px',color:'#f59e0b'}}>🛡️ 即时推送（阈值命中直推，绕Flash——救命规则）</div>
{(triggers as any).rules_instant.map((r:any)=><div key={r.id} style={{fontSize:'0.82em',padding:'4px 8px',marginBottom:2,borderRadius:4,background:'rgba(245,158,11,0.04)',display:'flex',alignItems:'center',gap:6}}><span>🚨</span><span style={{flex:1}}>{r.name}: {r.display_cond}</span><span style={{fontSize:'0.75em',color:'#888',fontFamily:'monospace'}}>{r.id}</span></div>)}
</>}
{(triggers as any).rules_log?.length>0&&<>
<div style={{fontWeight:600,margin:'8px 0 6px',color:'#9ca3af'}}>⚪ 仅记录（基线偏离，不推送，供趋势分析）</div>
{(triggers as any).rules_log.map((r:any)=><div key={r.id} style={{fontSize:'0.82em',padding:'4px 8px',marginBottom:2,borderRadius:4,background:'rgba(156,163,175,0.04)',display:'flex',alignItems:'center',gap:6}}><span>📋</span><span style={{flex:1}}>{r.name}: {r.display_cond}</span><span style={{fontSize:'0.75em',color:'#888',fontFamily:'monospace'}}>{r.id}</span></div>)}
</>}
</div>

{/* 记录已由独立 tab('records')提供 —— 触发 tab 只保留规则+Flash摘要 */}
{triggers.flash_summary&&<div style={{marginTop:8,padding:8,borderRadius:4,background:'rgba(59,130,246,0.05)',fontSize:'0.85em'}}>💬 {triggers.flash_summary}</div>}
</div>}</div>)}

{tab==='thresholds'&&(<div><h4 style={{marginBottom:6}}>步数停滞（分钟）</h4><div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6,marginBottom:12}}>{(['at_work_min','outdoor_min','at_home_min']as const).map(k=>(<div key={k}><label style={lbl}>{{at_work_min:'上班',outdoor_min:'外出',at_home_min:'在家'}[k]}</label><input type="number" min={5} style={inp} value={settings.step_stagnation[k]} onChange={e=>saveSettings({...settings,step_stagnation:{...settings.step_stagnation,[k]:parseInt(e.target.value)||30}})}/></div>))}</div><h4 style={{marginBottom:6}}>心率阈值</h4><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6,marginBottom:12}}>{(['critical_high','sustained_high','abnormal_low','surge_delta']as const).map(k=>(<div key={k}><label style={lbl}>{{critical_high:'危险高位',sustained_high:'持续偏高',abnormal_low:'异常低位',surge_delta:'骤升差值'}[k]}</label><input type="number" min={20} max={200} style={inp} value={settings.heart_rate[k]} onChange={e=>saveSettings({...settings,heart_rate:{...settings.heart_rate,[k]:parseInt(e.target.value)||50}})}/></div>))}</div><h4 style={{marginBottom:6}}>屏幕/键鼠</h4><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:6,marginBottom:12}}><div><label style={lbl}>深夜开始(时)</label><input type="number" min={0} max={6} style={inp} value={settings.screen.late_night_start_hour} onChange={e=>saveSettings({...settings,screen:{...settings.screen,late_night_start_hour:parseInt(e.target.value)||0}})}/></div><div><label style={lbl}>深夜结束(时)</label><input type="number" min={0} max={12} style={inp} value={settings.screen.late_night_end_hour} onChange={e=>saveSettings({...settings,screen:{...settings.screen,late_night_end_hour:parseInt(e.target.value)||6}})}/></div></div><h4 style={{marginBottom:6}}>推送延迟（秒）</h4><div style={{marginBottom:12}}><div><label style={lbl}>对话中延迟推送</label><input type="number" min={30} max={600} style={inp} value={(settings as any).push_defer_seconds??120} onChange={e=>saveSettings({...(settings as any),push_defer_seconds:parseInt(e.target.value)||120})}/></div></div><h4 style={{marginBottom:6}}>冷却（秒）</h4><div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:6}}>{(['critical_sec','high_sec','medium_sec']as const).map(k=>(<div key={k}><label style={lbl}>{{critical_sec:'Critical',high_sec:'High',medium_sec:'Medium'}[k]}</label><input type="number" min={60} max={86400} style={inp} value={settings.cooldowns[k]} onChange={e=>saveSettings({...settings,cooldowns:{...settings.cooldowns,[k]:parseInt(e.target.value)||3600}})}/></div>))}</div></div>)}
</div></div></div>);
};
