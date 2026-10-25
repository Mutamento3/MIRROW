import { useState, useEffect } from 'react';
import { getApiBase } from '../config';
import SuggestionReview from '../components/SuggestionReview';
import { cognitionRequest } from '../components/cognitionApi';

const API_BASE = getApiBase();

// 世界书词条类型
interface WBEntryData {
  name: string;
  aliases: string[];
  keywords: string[];
  category: string;
  body: string;
  updated_at?: string;
  revision?: string;
}

function formatUpdatedAt(value?: string): string {
  if (!value) return '更新时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '更新时间未知';
  return `更新于 ${date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })}`;
}

interface WBSuggestionData {
  id: string;
  type: string;
  target_entry: string;
  proposed_name: string;
  proposed_keywords: string[];
  proposed_body: string;
  reason: string;
  source: string;
  status: string;
  created: string;
}

// 剥离「## 编辑记录」段——那是维护元数据，编辑时只改正文
function stripEditLog(body: string): string {
  const idx = body.indexOf('## 编辑记录');
  return idx >= 0 ? body.slice(0, idx).trim() : body;
}

export default function WorldBookPage({ onBack, embedded = false }: { onBack: () => void; embedded?: boolean }) {
  const [entries, setEntries] = useState<WBEntryData[]>([]);
  const [selected, setSelected] = useState<WBEntryData | null>(null);
  const [suggestions, setSuggestions] = useState<WBSuggestionData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detecting, setDetecting] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [tab, setTab] = useState<'entries' | 'suggestions'>('entries');
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  const [editRevision, setEditRevision] = useState<string | undefined>();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({type:'new_entry' as string, target_entry:'', name:'', keywords:'', body:'', reason:''});

  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [mobileEntry, setMobileEntry] = useState<WBEntryData | null>(null);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  function loadEntries() {
    return fetch(`${API_BASE}/api/world-book/entries`)
      .then(r => r.json())
      .then(d => { setEntries(d.entries || []); setSelected(old => old ? d.entries.find((e: WBEntryData) => e.name === old.name) || null : null); setMobileEntry(old => old ? d.entries.find((e: WBEntryData) => e.name === old.name) || null : null); });
  }

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/api/world-book/entries`).then(r => r.json()),
      fetch(`${API_BASE}/api/world-book/suggestions`).then(r => r.json()),
    ])
      .then(([ed, sd]) => {
        setEntries(ed.entries || []);
        setSuggestions(sd.suggestions || []);
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  function startEdit(entry: WBEntryData) {
    setEditRevision(entry.revision);
    setSelected(entry);
    setEditing(true);
    setEditText(stripEditLog(entry.body));
  }

  function saveEdit() {
    if (!selected) return;
    fetch(`${API_BASE}/api/world-book/entries/${encodeURIComponent(selected.name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: editText, revision: editRevision }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'updated') {
          setEditing(false);
          setSelected(prev => prev ? { ...prev, body: editText, revision: data.revision } : prev);
          loadEntries();
        } else {
          alert('保存失败: ' + (data.message || data.status || '未知错误'));
        }
      })
      .catch(e => alert('保存请求失败: ' + e.message));
  }

  function deleteEntry(entry: WBEntryData) {
    if (!window.confirm(`确定删除世界书词条「${entry.name}」？`)) return;
    fetch(`${API_BASE}/api/world-book/entries/${encodeURIComponent(entry.name)}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'deleted') {
          setSelected(null);
          setMobileEntry(null);
          setEditing(false);
          loadEntries();
        } else {
          alert('删除失败: ' + (data.message || data.status || '未知错误'));
        }
      })
      .catch(e => alert('删除请求失败: ' + e.message));
  }

  function handleApply(sid: string) { setReviewId(sid); }

  function handleReject(sid: string) {
    fetch(`${API_BASE}/api/world-book/suggestions/${sid}/reject`, { method: 'POST' })
      .then(() => setSuggestions(prev => prev.filter(s => s.id !== sid)));
  }

  function handleUpdateSuggestion(sid: string) {
    fetch(`${API_BASE}/api/world-book/suggestions/${sid}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: editForm.type,
        target_entry: editForm.target_entry,
        proposed_name: editForm.name,
        proposed_keywords: editForm.keywords.split(',').map(k => k.trim()).filter(k => k),
        proposed_body: editForm.body,
        reason: editForm.reason,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'updated') {
          setSuggestions(prev => prev.map(s => s.id === sid ? {...s,
            type: editForm.type,
            target_entry: editForm.target_entry,
            proposed_name: editForm.name,
            proposed_keywords: editForm.keywords.split(',').map(k => k.trim()).filter(k => k),
            proposed_body: editForm.body,
            reason: editForm.reason,
          } : s));
          setEditingId(null);
        }
      })
      .catch(e => alert('更新失败: ' + e.message));
  }

  function renderBody(body: string) {
    return body.split('\n').map((line, i) => {
      if (line.startsWith('## ')) return <h3 key={i} className="wb-section-title">{line.replace('## ', '')}</h3>;
      if (line.startsWith('# ')) return null; // 跳过主标题（已在详情区显示）
      if (line.startsWith('- ')) return <li key={i} className="wb-bullet">{line.replace('- ', '')}</li>;
      if (line.trim() === '') return <div key={i} className="wb-spacer" />;
      return <p key={i} className="wb-paragraph">{line}</p>;
    });
  }

  // 按分类分组
  const categories = new Map<string, WBEntryData[]>();
  entries.forEach(e => {
    const c = e.category || '其他';
    if (!categories.has(c)) categories.set(c, []);
    categories.get(c)!.push(e);
  });

  if (loading) {
    return (
      <div className={embedded ? "cognition-legacy-book" : "wb-fullscreen"}>
        <div className="wb-container">
          <div className="wb-empty-state"><div className="wb-empty-icon">📖</div><p>加载中...</p></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={embedded ? "cognition-legacy-book" : "wb-fullscreen"}>
        <div className="wb-container">
          <div className="wb-empty-state"><div className="wb-empty-icon">❌</div><p>加载失败: {error}</p>
            <button className="btn-secondary" onClick={onBack}>{embedded ? "返回内容" : "返回聊天"}</button></div>
        </div>
      </div>
    );
  }

  return (
    <div className={embedded ? "cognition-legacy-book" : "wb-fullscreen"}>
      <div className="wb-container">
        <div className="wb-header">
          {reviewId && <SuggestionReview id={reviewId} onClose={() => setReviewId(null)} onApplied={() => {
            setSuggestions(prev => prev.filter(s => s.id !== reviewId)); setReviewId(null); loadEntries();
          }} />}
          <div className="wb-header-left">
            <button className="btn-secondary back-btn" onClick={onBack}>{embedded ? "← 返回内容" : "← 返回聊天"}</button>
            <h2>世界书</h2>
          </div>
          <div className="wb-tabs">
            <button className={`wb-tab-btn${tab === 'entries' ? ' active' : ''}`} onClick={() => setTab('entries')}>词条浏览</button>
            <button className={`wb-tab-btn${tab === 'suggestions' ? ' active' : ''}`} onClick={() => setTab('suggestions')}>
              待审建议 {suggestions.length > 0 && `(${suggestions.length})`}
            </button>
            <button className="wb-detect-btn" disabled={detecting} onClick={async () => {
              if (!window.confirm('分析最近活跃日，会调用模型并可能自动补充认知词条。继续？')) return;
              setDetecting(true);
              try {
                const d = await cognitionRequest('/maintenance/run', {}, 'POST', 420000);
                const status = d.status === 'completed' ? '完成' : d.status === 'completed_with_rejections' ? '已完成可核实内容，少量候选已隔离' : d.status;
                alert(`认知更新：${status}，${d.items_count} 条记录。请到当前书的“更新记录”查看依据与结果。`);
                await loadEntries();
              } catch (e) { alert((e as Error).message); }
              finally { setDetecting(false); }
            }}>{detecting ? '分析中…' : '🔍 检测与维护'}</button>
          </div>
        </div>

        {tab === 'entries' ? (
          <div className="wb-content">
            <div className="wb-sidebar">
              {Array.from(categories.entries()).map(([cat, items]) => (
                <div className="wb-group" key={cat}>
                  <h3 className="wb-group-title">{cat}</h3>
                  {items.map(e => (
                    <div key={e.name}
                      className={`wb-entry-item${selected?.name === e.name ? ' active' : ''}`}
                      onClick={() => {
                        setEditing(false);
                        if (isMobile) setMobileEntry(e); else setSelected(e);
                      }}>
                      <span className="wb-entry-name">{e.name}</span>
                      <span className="wb-entry-updated">{formatUpdatedAt(e.updated_at)}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="wb-detail">
              {selected ? (
                editing ? (
                  <div className="wb-entry-reader">
                    <h2 className="wb-entry-title">编辑: {selected.name}</h2>
                    <textarea
                      value={editText}
                      onChange={e => setEditText(e.target.value)}
                      rows={12}
                      style={{ width: '100%', padding: 12, fontSize: '0.95em', lineHeight: 1.8, border: '1px solid #444', borderRadius: 8, background: '#1e1e2e', color: '#eee', fontFamily: 'inherit' }}
                    />
                    <div className="wb-suggestion-actions" style={{ marginTop: 16 }}>
                      <button className="btn-primary" onClick={saveEdit}>保存</button>
                      <button className="btn-secondary" onClick={() => setEditing(false)}>取消</button>
                    </div>
                  </div>
                ) : (
                  <div className="wb-entry-reader">
                    <h2 className="wb-entry-title">{selected.name}</h2>
                    <p className="wb-entry-meta wb-entry-updated-detail">{formatUpdatedAt(selected.updated_at)}</p>
                    {selected.category && <p className="wb-entry-meta">分类: {selected.category}</p>}
                    {selected.keywords && <p className="wb-entry-meta">关键词: {Array.isArray(selected.keywords) ? selected.keywords.join(', ') : selected.keywords}</p>}
                    <div className="wb-entry-body">{renderBody(selected.body)}</div>
                    <div className="wb-suggestion-actions" style={{ marginTop: 16 }}>
                      <button className="btn-primary" onClick={() => startEdit(selected)}>✏️ 编辑</button>
                      <button className="btn-secondary" onClick={() => deleteEntry(selected)} style={{ color: '#ff6b6b' }}>🗑 删除</button>
                    </div>
                  </div>
                )
              ) : (
                <div className="wb-empty-state"><div className="wb-empty-icon">📖</div><p>从左侧选择一个词条查看</p></div>
              )}
            </div>
          </div>
        ) : (
          <div className="wb-content">
            <div className="wb-suggestions-list">
              {suggestions.length === 0 ? (
                <div className="wb-empty-state"><div className="wb-empty-icon">✨</div><p>暂无待审建议</p></div>
              ) : (
                suggestions.map(s => (
                  <div className="wb-suggestion-card" key={s.id}>
                    {editingId === s.id ? (
                      <div className="wb-suggestion-edit">
                        <label>类型: <select value={editForm.type} onChange={e => setEditForm({...editForm, type: e.target.value})}>
                          <option value="new_entry">新增词条</option>
                          <option value="update">修改词条</option>
                          <option value="supplement">补充词条</option>
                        </select></label>
                        {(editForm.type === 'update' || editForm.type === 'supplement') && (
                          <label>目标词条: <input value={editForm.target_entry} onChange={e => setEditForm({...editForm, target_entry: e.target.value})} placeholder="输入词条文件名" /></label>
                        )}
                        <label>词条名: <input value={editForm.name} onChange={e => setEditForm({...editForm, name: e.target.value})} /></label>
                        <label>关键词: <input value={editForm.keywords} onChange={e => setEditForm({...editForm, keywords: e.target.value})} /></label>
                        <label>正文: <textarea value={editForm.body} onChange={e => setEditForm({...editForm, body: e.target.value})} rows={4} /></label>
                        <label>理由: <input value={editForm.reason} onChange={e => setEditForm({...editForm, reason: e.target.value})} /></label>
                        <div className="wb-suggestion-actions">
                          <button className="btn-primary" onClick={() => handleUpdateSuggestion(s.id)}>保存</button>
                          <button className="btn-secondary" onClick={() => setEditingId(null)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="wb-suggestion-header">
                          <span className={`wb-suggestion-type wb-st-${s.type}`}>
                            {s.type === 'new_entry' ? '新增词条' : s.type === 'update' ? '修改词条' : '补充词条'}
                          </span>
                          <span className="wb-suggestion-name">{s.proposed_name}</span>
                        </div>
                        <div className="wb-suggestion-body">
                          <p><strong>关键词:</strong> {s.proposed_keywords.join(', ')}</p>
                          <pre className="wb-suggestion-text">{s.proposed_body}</pre>
                        </div>
                        <div className="wb-suggestion-meta">
                          <span>理由: {s.reason}</span>
                          <span>来源: {s.source}</span>
                        </div>
                        <div className="wb-suggestion-actions">
                          <button className="btn-primary" onClick={() => handleApply(s.id)}>采纳</button>
                          <button className="btn-secondary" onClick={() => handleReject(s.id)}>驳回</button>
                          <button className="btn-secondary" onClick={() => {
                            setEditingId(s.id);
                            setEditForm({ type: s.type || 'new_entry', target_entry: s.target_entry || '', name: s.proposed_name, keywords: s.proposed_keywords.join(', '), body: s.proposed_body, reason: s.reason });
                          }}>编辑</button>
                        </div>
                      </>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    {/* 移动端词条详情浮层 */}
    {isMobile && mobileEntry && (
      <div className="ledger-overlay" onClick={() => setMobileEntry(null)}>
        <div className="ledger-modal" style={{ maxWidth: "100vw", maxHeight: "90vh", width: "100%" }}
          onClick={e => e.stopPropagation()}>
          <div className="ledger-title">
            <span>{mobileEntry.name}</span>
            <button className="ledger-close-btn" onClick={() => setMobileEntry(null)}>✕</button>
          </div>
          <p className="wb-entry-meta wb-entry-updated-detail">{formatUpdatedAt(mobileEntry.updated_at)}</p>
          {mobileEntry.category && (
            <p style={{ fontSize: "0.85em", color: "#888", marginBottom: 12 }}>分类: {mobileEntry.category}</p>
          )}
          <div style={{ fontSize: "0.95em", lineHeight: 1.8 }}>{renderBody(mobileEntry.body)}</div>
          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <button className="btn-primary" onClick={() => {
              setEditing(true);
              setEditText(stripEditLog(mobileEntry.body));
              setSelected(mobileEntry);
              setMobileEntry(null);
            }}>✏️ 编辑</button>
            <button className="btn-secondary" style={{ color: '#ff6b6b' }} onClick={() => deleteEntry(mobileEntry)}>🗑 删除</button>
          </div>
        </div>
      </div>
    )}
    </div>
  );
}
