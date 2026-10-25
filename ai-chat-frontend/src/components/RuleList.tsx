import React, { useState, useEffect } from 'react';
import { getApiBase } from '../config';

interface RuleItem { id:string;name:string;priority:string;cooldown_seconds:number;display_group:string;display_cond:string;trigger_type:string; }

interface Props { alerts: any[]; }

export const RuleList: React.FC<Props> = ({ alerts }) => {
  const [rules, setRules] = useState<RuleItem[]>([]);
  useEffect(() => { fetch(`${getApiBase()}/api/sentinel/rules`).then(r=>r.json()).then(d=>{setRules(d.rules||[]);}).catch(()=>{}); }, []);

  const lastTrigger: Record<string,string> = {};
  alerts.forEach((a:any) => { if (!lastTrigger[a.rule_id]) lastTrigger[a.rule_id] = a.timestamp?.slice(11,16) || ''; });

  return (<div>
    <h4 style={{margin:'12px 0 4px'}}>🛡️ 即时推送</h4>
    {rules.filter(r=>r.trigger_type==='instant').map(r=>{const t=lastTrigger[r.id];return(<div key={r.id} style={{display:'flex',justifyContent:'space-between',padding:'2px 6px',fontSize:'0.78em',borderRadius:2,background:t?'rgba(245,158,11,0.08)':'transparent'}}><span>{r.id} {r.name}</span><span style={{color:'#888'}}>{r.display_cond}</span><span style={{minWidth:50,textAlign:'right',color:t?'#f59e0b':'#888'}}>{t?`⚡${t}`:'—'}</span></div>);})}

    {rules.filter(r=>r.trigger_type==='log').map(r=>{return(<div key={r.id} style={{display:'flex',justifyContent:'space-between',padding:'2px 6px',fontSize:'0.78em',borderRadius:2}}><span>{r.id} {r.name}</span><span style={{color:'#888'}}>{r.display_cond}</span></div>);})}
  </div>);
};
