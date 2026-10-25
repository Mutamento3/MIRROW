import { useState, useEffect } from 'react';
import { getApiBase } from '../config';

const API_BASE = getApiBase();

// 自我书词条类型
interface SBEntryData {
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

// 剥离「## 编辑记录」段——那是自动维护的审核元数据，编辑时只改正文
function stripEditLog(body: string): string {
  const idx = body.indexOf('## 编辑记录');
  return idx >= 0 ? body.slice(0, idx).trim() : body;
}

export default function SelfBookPage({ onBack, embedded = false }: { onBack: () => void; embedded?: boolean }) {
  const [entries, setEntries] = useState<SBEntryData[]>([]);
  const [selected, setSelected] = useState<SBEntryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  const [editRevision, setEditRevision] = useState<string | undefined>();

  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [mobileEntry, setMobileEntry] = useState<SBEntryData | null>(null);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  function loadEntries() {
    fetch(`${API_BASE}/api/self-book/entries`)
      .then(r => r.json())
      .then(d => { setEntries(d.entries || []); setSelected(old => old ? d.entries.find((e: SBEntryData) => e.name === old.name) || null : null); setMobileEntry(old => old ? d.entries.find((e: SBEntryData) => e.name === old.name) || null : null); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }

  useEffect(loadEntries, []);

  function startEdit(entry: SBEntryData) {
    setEditRevision(entry.revision);
    setSelected(entry);
    setEditing(true);
    setEditText(stripEditLog(entry.body));
  }

  function saveEdit() {
    if (!selected) return;
    fetch(`${API_BASE}/api/self-book/entries/${encodeURIComponent(selected.name)}`, {
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

  function deleteEntry(entry: SBEntryData) {
    if (!window.confirm(`确定删除自我书词条「${entry.name}」？`)) return;
    fetch(`${API_BASE}/api/self-book/entries/${encodeURIComponent(entry.name)}`, { method: 'DELETE' })
      .then(r => r.json())
      .then(data => {
        if (data.status === 'deleted') {
          setSelected(null);
          setEditing(false);
          loadEntries();
        } else {
          alert('删除失败: ' + (data.message || data.status || '未知错误'));
        }
      })
      .catch(e => alert('删除请求失败: ' + e.message));
  }

  function renderBody(body: string) {
    return body.split('\n').map((line, i) => {
      if (line.startsWith('## ')) return <h3 key={i} className="wb-section-title">{line.replace('## ', '')}</h3>;
      if (line.startsWith('# ')) return null; // 跳过主标题
      if (line.startsWith('- ')) return <li key={i} className="wb-bullet">{line.replace('- ', '')}</li>;
      if (line.trim() === '') return <div key={i} className="wb-spacer" />;
      return <p key={i} className="wb-paragraph">{line}</p>;
    });
  }

  const categories = new Map<string, SBEntryData[]>();
  entries.forEach(e => {
    const c = e.category || '其他';
    if (!categories.has(c)) categories.set(c, []);
    categories.get(c)!.push(e);
  });

  if (loading) {
    return (
      <div className={embedded ? "cognition-legacy-book" : "wb-fullscreen"}>
        <div className="wb-container">
          <div className="wb-empty-state"><div className="wb-empty-icon">✨</div><p>加载中...</p></div>
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
          <div className="wb-header-left">
            <button className="btn-secondary back-btn" onClick={onBack}>{embedded ? "← 返回内容" : "← 返回聊天"}</button>
            <h2>自我书</h2>
          </div>
          <div className="wb-tabs">
            <span className="wb-hint">AI 的长期自我认知 · 自动生长 · 可编辑</span>
          </div>
        </div>

        <div className="wb-content">
          <div className="wb-sidebar">
            {Array.from(categories.entries()).map(([cat, items]) => (
              <div className="wb-group" key={cat}>
                <h3 className="wb-group-title">{cat}</h3>
                {items.map(e => (
                  <div key={e.name}
                    className={`wb-entry-item${selected?.name === e.name ? ' active' : ''}`}
                    onClick={() => { setEditing(false); if (isMobile) setMobileEntry(e); else setSelected(e); }}>
                    <span className="wb-entry-name">{e.name}</span>
                    <span className="wb-entry-updated">{formatUpdatedAt(e.updated_at)}</span>
                  </div>
                ))}
              </div>
            ))}
            {entries.length === 0 && (
              <div className="wb-empty-state"><div className="wb-empty-icon">✨</div><p>暂无词条，AI 会在对话中自动生长</p></div>
            )}
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
              <div className="wb-empty-state"><div className="wb-empty-icon">✨</div><p>从左侧选择一个词条查看</p></div>
            )}
          </div>
        </div>
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
              <button className="btn-primary" onClick={() => { setEditing(true); setEditText(stripEditLog(mobileEntry.body)); setMobileEntry(null); setSelected(mobileEntry); }}>✏️ 编辑</button>
              <button className="btn-secondary" style={{ color: '#ff6b6b' }} onClick={() => deleteEntry(mobileEntry)}>🗑 删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
