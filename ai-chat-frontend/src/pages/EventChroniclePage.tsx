import { useState, useEffect } from 'react';
import { getApiBase } from '../config';
import DiarySourceStatus from '../components/DiarySourceStatus';
import MemoryBoxesPanel from '../components/MemoryBoxesPanel';
import type { MemoryBox } from '../components/MemoryBoxesPanel';

const API_BASE = getApiBase();

type MemorialSuggestion = {
  id: string;
  title: string;
  description: string;
  date_string: string;
};

type DiaryEntry = {
  id: string;
  date: string;
  content: string;
  summary_ids: string[];
  generated_at: string;
};

type ImportantEvent = {
  id: string;
  date_string: string;
  date_precision: 'full' | 'year_month' | 'year_only';
  event_text: string;
  emotion: string;
  created_at: string;
  updated_at: string;
};

export default function EventChroniclePage({ onBack, pendingMemorial, onAcceptMemorial, onRejectMemorial }: {
  onBack: () => void;
  pendingMemorial: MemorialSuggestion | null;
  onAcceptMemorial: () => void;
  onRejectMemorial: () => void;
}) {
  const [diaries, setDiaries] = useState<DiaryEntry[]>([]);
  const [selectedDiary, setSelectedDiary] = useState<DiaryEntry | null>(null);
  const [events, setEvents] = useState<ImportantEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsLoaded, setEventsLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [eventsError, setEventsError] = useState('');
  const [activeSection, setActiveSection] = useState<'diary' | 'events'>('diary');
  const [diaryOffset, setDiaryOffset] = useState(0);
  const [diaryHasMore, setDiaryHasMore] = useState(true);
  const [diaryLoadingMore, setDiaryLoadingMore] = useState(false);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [mobileDiary, setMobileDiary] = useState<DiaryEntry | null>(null);
  const [memoryBoxes, setMemoryBoxes] = useState<MemoryBox[]>([]);
  const [memoryBoxesLoading, setMemoryBoxesLoading] = useState(false);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // 新增/编辑重要事件表单
  const [showEventForm, setShowEventForm] = useState(false);
  const [editingEvent, setEditingEvent] = useState<ImportantEvent | null>(null);
  const [formDate, setFormDate] = useState('');
  const [formPrecision, setFormPrecision] = useState<'full' | 'year_month' | 'year_only'>('full');
  const [formText, setFormText] = useState('');
  const [formEmotion, setFormEmotion] = useState('');

  const fetchDiaries = async (append = false) => {
    setLoadError('');
    try {
      const off = append ? diaryOffset + 30 : 0;
      const res = await fetch(`${API_BASE}/events/diary?limit=30&offset=${off}`);
      if (!res.ok) throw new Error('日记暂时无法读取');
      if (res.ok) {
        const data = await res.json();
        if (append) { setDiaries(prev => [...prev, ...data]); setDiaryOffset(off); }
        else { setDiaries(data); setDiaryOffset(0); }
        setDiaryHasMore(data.length >= 30);
      }
    } catch { setLoadError('日记暂时无法读取，请重试'); }
  };
  const loadMoreDiaries = async () => { setDiaryLoadingMore(true); await fetchDiaries(true); setDiaryLoadingMore(false); };

  const fetchEvents = async () => {
    setEventsLoading(true); setEventsError('');
    try {
      const res = await fetch(`${API_BASE}/events/important?limit=50&offset=0`);
      if (!res.ok) throw new Error('重要事件暂时无法读取');
      if (res.ok) {
        const data = await res.json();
        setEvents(data);
        setEventsLoaded(true);
      }
    } catch { setEventsError('重要事件暂时无法读取，请重试'); }
    finally { setEventsLoading(false); }
  };

  useEffect(() => {
    setLoading(true);
    void fetchDiaries().finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (activeSection === 'events' && !eventsLoaded) void fetchEvents();
  }, [activeSection]);

  const openDiary = async (date: string) => {
    setMemoryBoxesLoading(true);
    try {
      const [diaryRes, boxesRes] = await Promise.all([
        fetch(`${API_BASE}/events/diary/${date}`),
        fetch(`${API_BASE}/events/memory-boxes/${date}`),
      ]);
      if (diaryRes.ok) setSelectedDiary(await diaryRes.json());
      setMemoryBoxes(boxesRes.ok ? (await boxesRes.json()).boxes || [] : []);
    } catch (e) { console.error('获取日记详情失败:', e); }
    finally { setMemoryBoxesLoading(false); }
  };

  const openMobileDiary = async (entry: DiaryEntry) => {
    setMobileDiary(entry);
    setMemoryBoxes([]);
    setMemoryBoxesLoading(true);
    try {
      const res = await fetch(`${API_BASE}/events/memory-boxes/${entry.date}`);
      setMemoryBoxes(res.ok ? (await res.json()).boxes || [] : []);
    } catch (e) { console.error('获取记忆盒失败:', e); }
    finally { setMemoryBoxesLoading(false); }
  };

  const submitEvent = async () => {
    if (!formText.trim() || !formDate.trim()) return;
    const body = {
      date_string: formDate,
      date_precision: formPrecision,
      event_text: formText.trim(),
      emotion: formEmotion.trim()
    };
    try {
      if (editingEvent) {
        const res = await fetch(`${API_BASE}/events/important/${editingEvent.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (res.ok) await fetchEvents();
      } else {
        const res = await fetch(`${API_BASE}/events/important`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (res.ok) await fetchEvents();
      }
      resetForm();
    } catch (e) { console.error('保存重要事件失败:', e); }
  };

  const deleteEvent = async (id: string) => {
    if (!window.confirm('确定要删除这个重要事件吗？')) return;
    try {
      const res = await fetch(`${API_BASE}/events/important/${id}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchEvents();
        setEditingEvent(null);
      }
    } catch (e) { console.error('删除重要事件失败:', e); }
  };

  const editEvent = (ev: ImportantEvent) => {
    setEditingEvent(ev);
    setFormDate(ev.date_string);
    setFormPrecision(ev.date_precision);
    setFormText(ev.event_text);
    setFormEmotion(ev.emotion);
    setShowEventForm(true);
  };

  const resetForm = () => {
    setShowEventForm(false);
    setEditingEvent(null);
    setFormDate('');
    setFormPrecision('full');
    setFormText('');
    setFormEmotion('');
  };

  const formatDate = (dateStr: string) => {
    if (dateStr.endsWith('-00-00')) return dateStr.replace('-00-00', '年');
    if (dateStr.endsWith('-00')) return dateStr.replace('-00', '');
    return dateStr;
  };

  return (
    <div className="events-fullscreen">
      <div className="events-container">
        <div className="events-header">
          <div className="events-header-left">
            <button className="btn-secondary back-btn" onClick={onBack}>
              ← 返回聊天
            </button>
            <h2>事件纪</h2>
          </div>
          <div className="events-controls">
            <button
              className={`events-tab-btn ${activeSection === 'diary' ? 'active' : ''}`}
              onClick={() => setActiveSection('diary')}
            >
              日记本
            </button>
            <button
              className={`events-tab-btn ${activeSection === 'events' ? 'active' : ''}`}
              onClick={() => setActiveSection('events')}
            >
              重要事件
            </button>
          </div>
        </div>

        <div className="events-content">
          {activeSection === 'diary' ? (
            /* ── 日记本 ── */
            <div className="diary-layout">
              <div className="diary-sidebar">
                <h3>日记列表</h3>
                {loadError && <p role="alert">{loadError}<button onClick={() => void fetchDiaries()}>重试</button></p>}
                {loading ? <p role="status">正在读取日记…</p> : !loadError && diaries.length === 0 ? (
                  <p className="events-empty">暂无日记记录</p>
                ) : (
                  <div className="diary-list">
                    {diaries.map(entry => (
                      <div
                        key={entry.id}
                        className={`diary-item ${selectedDiary?.id === entry.id ? 'active' : ''}`}
                        onClick={() => { if (isMobile) { void openMobileDiary(entry); } else { void openDiary(entry.date); } }}
                      >
                        <div className="diary-item-date">{entry.date}</div>
                        <div className="diary-item-preview">{entry.content.slice(0, 40)}...</div>
                      </div>
                    ))}
                  </div>
                )}
                {!loading && !loadError && diaryHasMore && (
                  <button className="btn-secondary" onClick={loadMoreDiaries} disabled={diaryLoadingMore}
                    style={{ marginTop: 8, width: "100%", padding: "6px 0", fontSize: "0.85em" }}>
                    {diaryLoadingMore ? "加载中..." : "📜 加载更早的日记"}
                  </button>
                )}
              </div>
              <div className="diary-content">
                {selectedDiary ? (
                  <div className="diary-entry-reader">
                    <h3 className="diary-entry-title">{selectedDiary.date} 的日记</h3>
                    <div className="diary-entry-time">
                      生成于 {new Date(selectedDiary.generated_at).toLocaleString()}
                    </div>
                    <DiarySourceStatus date={selectedDiary.date} />
                    <div className="diary-entry-body">{selectedDiary.content}</div>
                    <MemoryBoxesPanel boxes={memoryBoxes} loading={memoryBoxesLoading} />
                  </div>
                ) : (
                  <div className="events-empty-state">
                    <div className="events-empty-icon">📖</div>
                    <p>从左侧选择一篇日记查看</p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* ── 重要事件 ── */
            <div className="events-layout">
              <div className="events-sidebar">
                <div className="events-sidebar-header">
                  <h3>事件时间轴</h3>
                  <button className="btn-primary" onClick={() => { resetForm(); setShowEventForm(true); }}>
                    + 添加
                  </button>
                </div>
                {eventsError && <p role="alert">{eventsError}<button onClick={() => void fetchEvents()}>重试</button></p>}
                {eventsLoading ? <p role="status">正在读取重要事件…</p> : !eventsError && events.length === 0 ? (
                  <p className="events-empty">暂无重要事件</p>
                ) : (
                  <div className="events-timeline">
                    {events.map(ev => (
                      <div
                        key={ev.id}
                        className={`event-item ${editingEvent?.id === ev.id ? 'editing' : ''}`}
                        onClick={() => editEvent(ev)}
                      >
                        <div className="event-item-date">{formatDate(ev.date_string)}</div>
                        <div className="event-item-text">{ev.event_text}</div>
                        {ev.emotion && <div className="event-item-emotion">{ev.emotion}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="events-detail">
                {showEventForm ? (
                  <div className="event-form">
                    <h3>{editingEvent ? '编辑重要事件' : '添加重要事件'}</h3>
                    <div className="form-group">
                      <label>日期</label>
                      <input
                        type="text"
                        className="form-input"
                        value={formDate}
                        onChange={e => setFormDate(e.target.value)}
                        placeholder={formPrecision === 'full' ? '1999-06-04' : formPrecision === 'year_month' ? '1999-06' : '1999'}
                      />
                    </div>
                    <div className="form-group">
                      <label>日期精度</label>
                      <select
                        className="form-input"
                        value={formPrecision}
                        onChange={e => setFormPrecision(e.target.value as any)}
                      >
                        <option value="full">精确日期 (YYYY-MM-DD)</option>
                        <option value="year_month">年月 (YYYY-MM)</option>
                        <option value="year_only">仅年份 (YYYY)</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label>事件描述</label>
                      <textarea
                        className="form-textarea"
                        value={formText}
                        onChange={e => setFormText(e.target.value)}
                        rows={3}
                        placeholder="描述这个重要事件..."
                      />
                    </div>
                    <div className="form-group">
                      <label>情感标签（可选）</label>
                      <input
                        type="text"
                        className="form-input"
                        value={formEmotion}
                        onChange={e => setFormEmotion(e.target.value)}
                        placeholder="例如: 开心、遗憾、怀念"
                      />
                    </div>
                    <div className="event-form-actions">
                      <button className="btn-primary" onClick={submitEvent}>
                        {editingEvent ? '保存修改' : '添加'}
                      </button>
                      {editingEvent && (
                        <button className="btn-danger" onClick={() => deleteEvent(editingEvent.id)}>
                          删除
                        </button>
                      )}
                      <button className="btn-secondary" onClick={resetForm}>取消</button>
                    </div>
                  </div>
                ) : (
                  <div className="events-empty-state">
                    <div className="events-empty-icon">📜</div>
                    <p>点击左侧事件查看详情或编辑</p>
                    <button className="btn-primary" onClick={() => { resetForm(); setShowEventForm(true); }}>
                      + 添加重要事件
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 纪念日确认弹窗 ── */}
      {pendingMemorial && (
        <>
          <div className="confirm-backdrop" onClick={onRejectMemorial} />
          <div className="confirm-dialog-overlay">
            <div className="confirm-dialog">
              <div className="confirm-icon">💝</div>
              <h3>AI想把这个日子作为纪念日纪念下来</h3>
              <div className="confirm-event-info">
                <div className="confirm-event-title">{pendingMemorial.title}</div>
                {pendingMemorial.description && (
                  <div className="confirm-event-desc">{pendingMemorial.description}</div>
                )}
                <div className="confirm-event-date">{pendingMemorial.date_string.replace('0000-', '')}</div>
              </div>
              <div className="confirm-actions">
                <button className="btn-confirm-accept" onClick={onAcceptMemorial}>同意</button>
                <button className="btn-confirm-reject" onClick={onRejectMemorial}>拒绝</button>
              </div>
            </div>
          </div>
        </>
      )}
    {/* 移动端日记详情浮层 */}
    {isMobile && mobileDiary && (
      <div className="ledger-overlay" onClick={() => setMobileDiary(null)}>
        <div className="ledger-modal" style={{ maxWidth: "100vw", maxHeight: "90vh", width: "100%" }}
          onClick={e => e.stopPropagation()}>
          <div className="ledger-title">
            <span>{mobileDiary.date} 的日记</span>
            <button className="ledger-close-btn" onClick={() => setMobileDiary(null)}>✕</button>
          </div>
          <div style={{ fontSize: "0.85em", color: "#888", marginBottom: 12 }}>
            生成于 {new Date(mobileDiary.generated_at).toLocaleString()}
          </div>
          <DiarySourceStatus date={mobileDiary.date} />
          <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.8, fontSize: "0.95em" }}>{mobileDiary.content}</div>
          <MemoryBoxesPanel boxes={memoryBoxes} loading={memoryBoxesLoading} />
        </div>
      </div>
    )}
    </div>
  );
}
