import { stripWikilinks } from '../utils';
import { useLedger } from '../hooks/useLedger';

function LedgerPanel({ onClose }: { onClose: () => void }) {
  const {
    ledgerEntries, ledgerPage, setLedgerPage, ledgerTotal, ledgerFilter, setLedgerFilter,
    disputingEntry, setDisputingEntry, disputeComment, setDisputeComment,
    detailEntry, setDetailEntry, detailLoading,
    getExpiryDays, fetchLedger, handleDeleteLedger, handleSettleLedger, handleDisputeLedger, fetchDetail,
    LEDGER_PAGE_SIZE,
  } = useLedger();

  return (
    <>
      <div className="ledger-overlay" onClick={onClose}>
        <div className="ledger-modal" onClick={e => e.stopPropagation()}>
          <div className="ledger-title">
            <span>📒 记账本</span>
            <select value={ledgerFilter} onChange={v => { setLedgerFilter(v.target.value); setLedgerPage(0); fetchLedger(0, v.target.value); }} style={{marginLeft:8,fontSize:'0.75em',padding:'1px 4px',border:'1px solid var(--border-color)',borderRadius:4,background:'var(--bg-secondary)'}}>
              <option value="">全部类型</option>
              <option value="promise_broken">💔 答应没做到</option>
              <option value="hidden_truth">🫣 隐瞒被识破</option>
              <option value="serious_flirt">💋 正经场合撩</option>
              <option value="wander_check">📌 漫游核查</option>
            </select>
            <button className="ledger-close-btn" onClick={onClose}>✕</button>
          </div>
          {ledgerEntries.length === 0 ? (
            <div className="ledger-empty">暂无记录</div>
          ) : (
            <div className="ledger-list">
              {ledgerEntries.map(e => (
                <div key={e.id} className={`ledger-item${(e as any).comment ? ' disputed' : ''}`}>
                  <span className="ledger-type-label" data-tip={e.debt_type === 'promise_broken' ? '答应没做到' : e.debt_type === 'hidden_truth' ? '隐瞒被识破' : e.debt_type === 'serious_flirt' ? '正经场合撩' : e.debt_type}>{e.debt_type === 'promise_broken' ? '💔' : e.debt_type === 'hidden_truth' ? '🫣' : e.debt_type === 'serious_flirt' ? '💋' : '📌'}</span>
                  <span className="ledger-content" data-tip={e.content}>{e.content}</span>
                  <span className="ledger-date">{e.created.slice(0, 10)}</span>
                  {(() => { const ed = getExpiryDays(e); if (ed !== null && ed <= 3) return <span style={{fontSize:'0.7em',color:ed<=1?'#e74c3c':'#f39c12',marginRight:4}}>{ed<=0?'今日到期':`${ed}天后`}</span>; return null; })()}
                  <button className="ledger-detail-btn" title="详情" onClick={() => fetchDetail(e.id)} style={{background:'none',border:'none',cursor:'pointer',fontSize:'0.85em',opacity:0.6,padding:'0 2px'}}>🔍</button>
                  <button className="ledger-settle-btn" title="结算——这事过去了" onClick={() => handleSettleLedger(e.id)} style={{background:'none',border:'none',cursor:'pointer',fontSize:'0.85em',opacity:0.6,padding:'0 2px',color:'#27ae60'}}>✅</button>
                  <button className="ledger-dispute-btn" title="争议" onClick={() => { setDisputingEntry(e.id); setDisputeComment((e as any).comment || ''); }} style={{background:'none',border:'none',cursor:'pointer',fontSize:'0.85em',opacity:0.6,padding:'0 2px'}}>⚠️</button>
                  <button className="ledger-del-btn" title="删除" onClick={() => handleDeleteLedger(e.id)}>✕</button>
                  {(e as any).comment && (
                    <div className="ledger-comment">人类伙伴: {(e as any).comment}</div>
                  )}
                  {disputingEntry === e.id && (
                    <div className="ledger-dispute-input" style={{width:'100%',marginTop:4}}>
                      <input value={disputeComment} onChange={v => setDisputeComment(v.target.value)} maxLength={50} placeholder="备注（≤50字）" style={{width:'70%',fontSize:'0.8em',padding:'2px 6px'}} />
                      <button onClick={() => handleDisputeLedger(e.id)} style={{marginLeft:4,fontSize:'0.8em'}}>保存</button>
                      <button onClick={() => setDisputingEntry(null)} style={{marginLeft:2,fontSize:'0.8em'}}>取消</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {ledgerTotal > LEDGER_PAGE_SIZE && (
            <div className="ledger-pagination">
              <button disabled={ledgerPage === 0} onClick={() => { const p = ledgerPage - 1; setLedgerPage(p); fetchLedger(p); }}>‹ 上一页</button>
              <span>{ledgerPage + 1} / {Math.ceil(ledgerTotal / LEDGER_PAGE_SIZE)}</span>
              <button disabled={(ledgerPage + 1) * LEDGER_PAGE_SIZE >= ledgerTotal} onClick={() => { const p = ledgerPage + 1; setLedgerPage(p); fetchLedger(p); }}>下一页 ›</button>
            </div>
          )}
        </div>
      </div>

      {detailEntry && (
        <div className="ledger-overlay" onClick={() => setDetailEntry(null)}>
          <div className="ledger-modal ledger-detail-modal" onClick={e => e.stopPropagation()}>
            <div className="ledger-title">
              <span>📝 账目详情</span>
              <button className="ledger-close-btn" onClick={() => setDetailEntry(null)}>✕</button>
            </div>
            {detailLoading ? (
              <div className="ledger-empty">加载中...</div>
            ) : detailEntry.entry ? (
              <div style={{fontSize:'0.85em',lineHeight:1.6}}>
                <div style={{marginBottom:8}}>
                  <strong>[{detailEntry.entry.debt_type}]</strong> {detailEntry.entry.content}
                </div>
                {detailEntry.entry.comment && (
                  <div className="ledger-comment" style={{marginBottom:8,borderLeft:'3px solid #f59e0b',paddingLeft:8}}>
                    ⚠️ 人类伙伴备注: {detailEntry.entry.comment}
                  </div>
                )}
                <div style={{color:'#888',fontSize:'0.75em',marginBottom:12}}>
                  记录时间: {detailEntry.entry.created}
                  {detailEntry.entry.last_active && detailEntry.entry.last_active !== detailEntry.entry.created && (
                    <> · 最后活跃: {detailEntry.entry.last_active.slice(0, 16)}</>
                  )}
                  {detailEntry.entry.settled_at && (
                    <span style={{color:'#27ae60'}}> · 已结算: {detailEntry.entry.settled_at.slice(0, 16)}</span>
                  )}
                  {detailEntry.entry.status === 'dormant' && (
                    <span style={{color:'#e67e22'}}> · 已过期</span>
                  )}
                </div>
                {detailEntry.entry?.context_messages && detailEntry.entry.context_messages.length > 0 ? (
                  <div>
                    <div style={{fontWeight:600,marginBottom:4}}>📜 当时对话:</div>
                    {detailEntry.entry.context_messages.map((msg: any, i: number) => (
                      <div key={i} style={{marginBottom:4,paddingLeft:8,borderLeft:'2px solid #e5e7eb'}}>
                        <span style={{color:'#888',fontSize:'0.7em'}}>
                          {msg.role === 'user' ? '人类伙伴说' : '我回应'}:
                        </span>{' '}
                        <span>{stripWikilinks(msg.content)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{color:'#888'}}>📜 无存档上下文</div>
                )}
              </div>
            ) : (
              <div className="ledger-empty">加载失败</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

export default LedgerPanel;
