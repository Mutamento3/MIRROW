import { useState } from 'react';
import { getApiBase } from '../config';
import { usePersonaEvo } from '../hooks/usePersonaEvo';

const VALIDITY_OPTIONS = [0, 3, 5, 7, 14, 30];

type ValidityEdit = {
  mode: 'reactivate' | 'adjust';
  suggestion: any;
  expiresDays: number;
};

function PersonaEvoPanel({ onClose, embedded = false }: { onClose: () => void; embedded?: boolean }) {
  const {
    personaSuggestions, personaEvoTab, setPersonaEvoTab,
    activeEvolutions, migratePreview, setMigratePreview,
    adoptConfirm, setAdoptConfirm, historyEvolutions,
    historyExpandedMonths, setHistoryExpandedMonths,
    fetchPersonaSuggestions, fetchActiveEvolutions, fetchMigratePreview, fetchHistoryEvolutions,
  } = usePersonaEvo();
  const [validityEdit, setValidityEdit] = useState<ValidityEdit | null>(null);

  const close = () => { setAdoptConfirm(null); onClose(); };

  const openValidityEditor = (suggestion: any, mode: ValidityEdit['mode']) => {
    const rawDays = Number(suggestion.expires_days);
    const expiresDays = Number.isFinite(rawDays)
      ? Math.max(0, Math.floor(rawDays))
      : (mode === 'reactivate' ? 7 : 0);
    setValidityEdit({ mode, suggestion, expiresDays });
  };

  const submitValidityEdit = async () => {
    if (!validityEdit) return;
    const { mode, suggestion, expiresDays } = validityEdit;
    const endpoint = mode === 'reactivate'
      ? `/api/persona/suggestions/${suggestion.id}/reactivate`
      : `/api/persona/suggestions/${suggestion.id}/validity`;
    try {
      const res = await fetch(`${getApiBase()}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expires_days: expiresDays }),
      });
      const data = await res.json();
      if (data.status !== (mode === 'reactivate' ? 'reactivated' : 'updated')) {
        alert((mode === 'reactivate' ? '复启用' : '调整有效期') + '失败: ' + (data.message || data.status || '未知错误'));
        return;
      }
      setValidityEdit(null);
      await Promise.all([fetchPersonaSuggestions(), fetchActiveEvolutions(), fetchHistoryEvolutions()]);
    } catch (e: any) {
      alert((mode === 'reactivate' ? '复启用' : '调整有效期') + '失败: ' + e.message);
    }
  };

  return (
    <>
      <div className={embedded ? "cognition-feedback" : "ledger-overlay"} onClick={embedded ? undefined : close}>
        <div className="ledger-modal" style={{maxWidth: embedded ? "100%" : 540}} onClick={e => e.stopPropagation()}>
          <div className="ledger-title">
            <span>🧬 互动反馈与约定</span>
            <small>人类伙伴对 AI 的互动偏好；AI 对自己的长期认识保存在自我书。</small>
            <button className="ledger-close-btn" onClick={close}>✕</button>
          </div>
          {/* Tabs */}
          <div className="bookmark-tabs" style={{marginBottom:8}}>
            <button className={`bookmark-tab ${personaEvoTab==='pending'?'active':''}`}
              onClick={() => { setPersonaEvoTab('pending'); setAdoptConfirm(null); }}>
              待处理 ({personaSuggestions.length})
            </button>
            <button className={`bookmark-tab ${personaEvoTab==='active'?'active':''}`}
              onClick={() => { setPersonaEvoTab('active'); fetchActiveEvolutions(); setAdoptConfirm(null); }}>
              活跃 ({activeEvolutions.length})
            </button>
            <button className={`bookmark-tab ${personaEvoTab==='history'?'active':''}`}
              onClick={() => { setPersonaEvoTab('history'); fetchHistoryEvolutions(); setAdoptConfirm(null); }}>
              历史
            </button>
            <button className={`bookmark-tab ${personaEvoTab==='migrate'?'active':''}`}
              onClick={() => { setPersonaEvoTab('migrate'); fetchMigratePreview(); setAdoptConfirm(null); }}>
              迁移
            </button>
          </div>

          {/* Pending tab */}
          {personaEvoTab === 'pending' && (
            personaSuggestions.length === 0 ? (
              <div className="ledger-empty">暂无待处理建议（每日话题结束时自动检测）</div>
            ) : (
              <div className="ledger-list">
                {personaSuggestions.map((s: any, i: number) => {
                  const sType = s.type || 'add';
                  const typeLabel = sType==='add'?'➕ 新增':sType==='modify'?'✏️ 修改':'🗑️ 移除';
                  const section = s.target_section || '全局';
                  const proposed = s.proposed_text || s.suggested_change || '';
                  return (
                    <div key={s.id || i} className="ledger-item" style={{flexDirection:'column',alignItems:'stretch'}}>
                      <div style={{fontWeight:600,fontSize:'0.8em'}}>
                        {typeLabel} <span style={{color:'#888'}}>[{section}]</span> {s.title}
                        <span style={{marginLeft:6,color:'#888',fontSize:'0.7em'}}>置信度 {Math.round((s.confidence||0)*100)}%</span>
                      </div>
                      {s.evidence && <div style={{fontSize:'0.7em',color:'#999',marginTop:2}}>📋 {s.evidence}</div>}
                      <div style={{fontSize:'0.8em',color:'#4a9eff',marginTop:4,background:'rgba(74,158,255,0.06)',padding:'6px 8px',borderRadius:4}}>
                        {proposed}
                      </div>
                      <div style={{marginTop:6,display:'flex',gap:6,alignItems:'center'}}>
                        <button onClick={async () => {
                          // 先检查冲突
                          let conflict: any = null;
                          try {
                            const cr = await fetch(`${getApiBase()}/api/persona/conflict/${s.id ?? i}`);
                            const cd = await cr.json();
                            conflict = cd.conflict;
                          } catch {}
                          setAdoptConfirm({index: i, suggestion: s, conflict});
                        }} style={{fontSize:'0.75em',padding:'2px 10px',borderRadius:4,border:'1px solid #4a9eff',background:'transparent',color:'#4a9eff',cursor:'pointer'}}>采纳</button>
                        <button onClick={async () => {
                          const sid = s.id ?? i;
                          try {
                            const res = await fetch(`${getApiBase()}/api/persona/suggestions/${sid}/reject`, {method:'POST'});
                            const data = await res.json();
                            if (!res.ok || data.status !== 'rejected') {
                              alert('驳回失败: ' + (data.message || data.status || '未知错误'));
                              return;
                            }
                            await Promise.all([fetchPersonaSuggestions(), fetchHistoryEvolutions()]);
                          } catch (e: any) {
                            alert('驳回失败: ' + e.message);
                          }
                        }} style={{fontSize:'0.75em',padding:'2px 10px',borderRadius:4,border:'1px solid #888',background:'transparent',color:'#888',cursor:'pointer'}}>驳回</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )
          )}

          {/* Active tab */}
          {personaEvoTab === 'active' && (
            activeEvolutions.length === 0 ? (
              <div className="ledger-empty">暂无活跃的演化条目</div>
            ) : (
              <div className="ledger-list">
                {activeEvolutions.map((s: any) => {
                  const section = s.target_section || '全局';
                  const proposed = s.proposed_text || s.suggested_change || '';
                  // 计算剩余天数
                  let daysLeft = -1;
                  if (s.expires_at) {
                    const exp = new Date(s.expires_at);
                    const now = new Date();
                    daysLeft = Math.ceil((exp.getTime() - now.getTime()) / (1000*60*60*24));
                  }
                  return (
                    <div key={s.id} className="ledger-item" style={{flexDirection:'column',alignItems:'stretch'}}>
                      <div style={{fontWeight:600,fontSize:'0.8em'}}>
                        <span style={{color:'#888'}}>[{section}]</span> {s.title}
                        {s.expires_at ? (
                          <span style={{marginLeft:6,fontSize:'0.7em',color:daysLeft<=3?'#e88':'#888'}}>
                            {daysLeft <= 0 ? '⏰ 今日到期' : `⏳ 剩余 ${daysLeft} 天`}
                          </span>
                        ) : (
                          <span style={{marginLeft:6,fontSize:'0.7em',color:'#888'}}>♾️ 永久</span>
                        )}
                      </div>
                      <div style={{fontSize:'0.8em',color:'#ccc',marginTop:4,background:'rgba(255,255,255,0.03)',padding:'6px 8px',borderRadius:4}}>
                        {proposed.length > 80 ? proposed.slice(0,80)+'...' : proposed}
                      </div>
                      <div style={{marginTop:4,display:'flex',gap:6}}>
                        <button onClick={() => openValidityEditor(s, 'adjust')}
                          style={{fontSize:'0.7em',padding:'1px 8px',borderRadius:3,border:'1px solid #4a9eff',background:'transparent',color:'#4a9eff',cursor:'pointer'}}>调整有效期</button>
                        <button onClick={async () => {
                          try {
                            const res = await fetch(`${getApiBase()}/api/persona/suggestions/${s.id}/expire`, {method:'POST'});
                            const data = await res.json();
                            if (!res.ok || data.status !== 'expired') {
                              alert('提前失效失败: ' + (data.message || data.status || '未知错误'));
                              return;
                            }
                            await Promise.all([fetchActiveEvolutions(), fetchHistoryEvolutions()]);
                          } catch (e: any) {
                            alert('提前失效失败: ' + e.message);
                          }
                        }} style={{fontSize:'0.7em',padding:'1px 8px',borderRadius:3,border:'1px solid #e88',background:'transparent',color:'#e88',cursor:'pointer'}}>提前失效</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )
          )}

          {/* History tab */}
          {personaEvoTab === 'history' && (
            (() => {
              // 按月份分组
              const groups: Record<string, any[]> = {};
              historyEvolutions.forEach((s: any) => {
                const month = (s.created_at || s.applied_at || '').slice(0,7) || '未知';
                if (!groups[month]) groups[month] = [];
                groups[month].push(s);
              });
              const sortedMonths = Object.keys(groups).sort().reverse();
              if (sortedMonths.length === 0) return <div className="ledger-empty">暂无历史记录</div>;
              return (
                <div className="ledger-list">
                  {sortedMonths.map(month => {
                    const isExpanded = historyExpandedMonths.has(month);
                    return (
                      <div key={month}>
                        <div onClick={() => {
                          setHistoryExpandedMonths(prev => {
                            const n = new Set(prev);
                            isExpanded ? n.delete(month) : n.add(month);
                            return n;
                          });
                        }} style={{fontSize:'0.8em',fontWeight:600,color:'#aaa',padding:'4px 0',cursor:'pointer',userSelect:'none'}}>
                          {isExpanded ? '▼' : '▶'} {month} ({groups[month].length})
                        </div>
                        {isExpanded && groups[month].map((s: any, i: number) => (
                          <div key={i} className="ledger-item" style={{flexDirection:'column',alignItems:'stretch',opacity:0.6}}>
                            <div style={{fontSize:'0.75em',fontWeight:600}}>
                              {s.status==='expired'?'⏰ 已过期':s.status==='rejected'?'❌ 已驳回':'📦 历史'} {s.title}
                            </div>
                            <div style={{fontSize:'0.7em',color:'#888',marginTop:2}}>
                              {(s.proposed_text || s.suggested_change || '').slice(0,60)}
                            </div>
                            {s.status === 'expired' && (
                              <div style={{marginTop:5}}>
                                <button onClick={() => openValidityEditor(s, 'reactivate')}
                                  style={{fontSize:'0.7em',padding:'1px 8px',borderRadius:3,border:'1px solid #4a9eff',background:'transparent',color:'#4a9eff',cursor:'pointer'}}>复启用</button>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
              );
            })()
          )}

          {/* Migrate tab */}
          {personaEvoTab === 'migrate' && (
            <div>
              <div style={{fontSize:'0.8em',color:'#aaa',marginBottom:8}}>
                将旧格式 <code>[人格演化 ...]</code> 片段统一为新风格。预览确认后再替换。
              </div>
              {migratePreview.length === 0 ? (
                <div className="ledger-empty">
                  暂无待迁移的旧片段
                  <button onClick={fetchMigratePreview} style={{marginLeft:8,fontSize:'0.8em',padding:'2px 8px',cursor:'pointer'}}>🔄 检查</button>
                </div>
              ) : (
                <>
                  <div className="ledger-list">
                    {migratePreview.map((item: any, i: number) => (
                      <div key={i} className="ledger-item" style={{flexDirection:'column',alignItems:'stretch'}}>
                        <div style={{fontWeight:600,fontSize:'0.75em',color:'#e88'}}>旧: [{item.old_title}] {item.old_content.slice(0,60)}...</div>
                        <div style={{fontSize:'0.8em',color:'#4a9eff',marginTop:4,background:'rgba(74,158,255,0.06)',padding:'6px 8px',borderRadius:4}}>
                          <span style={{fontSize:'0.7em',color:'#888'}}>[{item.new_target_section}]</span> {item.new_proposed_text}
                        </div>
                      </div>
                    ))}
                  </div>
                  <button onClick={async () => {
                    try {
                      const res = await fetch(`${getApiBase()}/api/persona/migrate/apply`, {
                        method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body: JSON.stringify({items: migratePreview}),
                      });
                      const data = await res.json();
                      if (data.status === 'ok') {
                        alert(`✅ 迁移完成：${data.applied} 条旧片段已转为新格式\n备份: ${data.backup}`);
                        setMigratePreview([]);
                        fetchActiveEvolutions();
                      } else {
                        alert('迁移失败: ' + (data.message || ''));
                      }
                    } catch (e: any) { alert('迁移失败: ' + e.message); }
                  }} style={{marginTop:8,padding:'6px 16px',borderRadius:6,border:'none',background:'#4a9eff',color:'white',cursor:'pointer',fontWeight:600}}>
                    确认迁移（共 {migratePreview.length} 条）
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 采纳确认弹窗 */}
      {adoptConfirm && (
        <div className="ledger-overlay" onClick={() => setAdoptConfirm(null)}>
          <div className="ledger-modal" style={{maxWidth:440}} onClick={e => e.stopPropagation()}>
            <div className="ledger-title">
              <span>确认采纳</span>
              <button className="ledger-close-btn" onClick={() => setAdoptConfirm(null)}>✕</button>
            </div>
            <div style={{fontSize:'0.85em',marginBottom:12}}>
              <div style={{fontWeight:600,marginBottom:4}}>{adoptConfirm.suggestion.title}</div>
              <div style={{color:'#4a9eff',background:'rgba(74,158,255,0.06)',padding:'8px',borderRadius:4}}>
                {adoptConfirm.suggestion.proposed_text || adoptConfirm.suggestion.suggested_change}
              </div>
            </div>
            {adoptConfirm.conflict && adoptConfirm.conflict.level === 'warn' && (
              <div style={{fontSize:'0.75em',color:'#e88',marginBottom:8,padding:'6px',background:'rgba(238,136,136,0.08)',borderRadius:4}}>
                ⚠️ 同段落 [{adoptConfirm.suggestion.target_section}] 下已有 {adoptConfirm.conflict.conflicts?.length || 0} 条活跃演化，请注意避免冲突。
              </div>
            )}
            <div style={{marginBottom:12}}>
              <label style={{fontSize:'0.8em',color:'#aaa'}}>有效期：</label>
              <select
                value={adoptConfirm.suggestion.expires_days ?? 7}
                onChange={e => {
                  const v = parseInt(e.target.value);
                  setAdoptConfirm(prev => prev ? {...prev, suggestion:{...prev.suggestion, expires_days:v}} : null);
                }}
                style={{marginLeft:8,fontSize:'0.8em',padding:'2px 6px',borderRadius:4,background:'var(--bg-secondary)',color:'var(--text-primary)',border:'1px solid #555'}}>
                <option value={0}>永久</option>
                <option value={3}>3 天</option>
                <option value={5}>5 天</option>
                <option value={7}>7 天</option>
                <option value={14}>14 天</option>
                <option value={30}>30 天</option>
              </select>
            </div>
            <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
              <button onClick={() => setAdoptConfirm(null)}
                style={{padding:'4px 14px',borderRadius:4,border:'1px solid #888',background:'transparent',color:'#888',cursor:'pointer'}}>取消</button>
              <button onClick={async () => {
                const s = adoptConfirm.suggestion;
                const sid = s.id ?? adoptConfirm.index;
                try {
                  const res = await fetch(`${getApiBase()}/api/persona/suggestions/${sid}/apply`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({expires_days: s.expires_days ?? 7}),
                  });
                  const data = await res.json();
                  if (!res.ok || data.status !== 'applied') {
                    alert('采纳失败: ' + (data.message || data.status || '未知错误'));
                    return;
                  }
                  setAdoptConfirm(null);
                  await Promise.all([
                    fetchPersonaSuggestions(),
                    fetchActiveEvolutions(),
                    fetchHistoryEvolutions(),
                  ]);
                  setPersonaEvoTab('active');
                } catch (e: any) {
                  alert('采纳失败: ' + e.message);
                }
              }} style={{padding:'4px 14px',borderRadius:4,border:'none',background:'#4a9eff',color:'white',cursor:'pointer',fontWeight:600}}>
                确认采纳
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 复启用 / 调整有效期 */}
      {validityEdit && (
        <div className="ledger-overlay" onClick={() => setValidityEdit(null)}>
          <div className="ledger-modal" style={{maxWidth:440}} onClick={e => e.stopPropagation()}>
            <div className="ledger-title">
              <span>{validityEdit.mode === 'reactivate' ? '复启用人格演化' : '调整启用期'}</span>
              <button className="ledger-close-btn" onClick={() => setValidityEdit(null)}>✕</button>
            </div>
            <div style={{fontSize:'0.85em',marginBottom:12}}>
              <div style={{fontWeight:600,marginBottom:4}}>{validityEdit.suggestion.title}</div>
              <div style={{color:'#4a9eff',background:'rgba(74,158,255,0.06)',padding:'8px',borderRadius:4}}>
                {validityEdit.suggestion.proposed_text || validityEdit.suggestion.suggested_change}
              </div>
            </div>
            <div style={{marginBottom:12}}>
              <label style={{fontSize:'0.8em',color:'#aaa'}}>有效期：</label>
              <select
                value={validityEdit.expiresDays}
                onChange={e => setValidityEdit(prev => prev ? {...prev, expiresDays: parseInt(e.target.value, 10)} : null)}
                style={{marginLeft:8,fontSize:'0.8em',padding:'2px 6px',borderRadius:4,background:'var(--bg-secondary)',color:'var(--text-primary)',border:'1px solid #555'}}>
                {[...new Set([...VALIDITY_OPTIONS, validityEdit.expiresDays])]
                  .sort((a, b) => a - b)
                  .map(days => <option key={days} value={days}>{days === 0 ? '永久' : `${days} 天`}</option>)}
              </select>
              <div style={{fontSize:'0.7em',color:'#888',marginTop:6}}>
                {validityEdit.mode === 'reactivate' ? '复启用后从现在开始计时。' : '调整后从现在开始重新计时。'}
              </div>
            </div>
            <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
              <button onClick={() => setValidityEdit(null)}
                style={{padding:'4px 14px',borderRadius:4,border:'1px solid #888',background:'transparent',color:'#888',cursor:'pointer'}}>取消</button>
              <button onClick={submitValidityEdit}
                style={{padding:'4px 14px',borderRadius:4,border:'none',background:'#4a9eff',color:'white',cursor:'pointer',fontWeight:600}}>
                {validityEdit.mode === 'reactivate' ? '确认复启用' : '保存有效期'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default PersonaEvoPanel;
