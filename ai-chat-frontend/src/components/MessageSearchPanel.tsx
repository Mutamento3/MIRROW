import { useEffect, useRef, useState } from 'react';
import { Virtuoso } from 'react-virtuoso';
import * as sessionApi from '../sessionApi';
import './MessageSearchPanel.css';

const MESSAGE_TYPES: { value: sessionApi.MessageSearchType; label: string }[] = [
  {value:'all',label:'全部'}, {value:'text',label:'纯文字'}, {value:'image',label:'图片'},
  {value:'voice',label:'语音'}, {value:'card',label:'小卡片'},
  {value:'event',label:'事件／通知'}, {value:'tool',label:'工具记录'},
];
const CARD_TYPES: { value: sessionApi.MessageCardSearchType; label: string }[] = [
  {value:'card',label:'全部卡片'}, {value:'card_post',label:'帖子'}, {value:'card_music',label:'音乐'},
  {value:'card_long_text',label:'长文'}, {value:'card_taobao',label:'淘宝'},
  {value:'card_wardrobe',label:'衣橱'}, {value:'card_report',label:'分析报告'}, {value:'card_other',label:'其他卡片'},
];
const typeLabel = (type: sessionApi.MessageSearchType) => [...MESSAGE_TYPES, ...CARD_TYPES].find(item => item.value === type)?.label || type;

interface MessageSearchPanelProps {
  sessionId: string;
  personaName: string;
  formatTimestamp: (d: Date) => string;
  onLocate: (messageId: string) => void;
  onClose: () => void;
}

function MessageSearchPanel({ sessionId, personaName, formatTimestamp, onLocate, onClose }: MessageSearchPanelProps) {
  const [query, setQuery] = useState('');
  const [messageType, setMessageType] = useState<sessionApi.MessageSearchType>('all');
  const [cardType, setCardType] = useState<sessionApi.MessageCardSearchType>('card');
  const effectiveType = messageType === 'card' ? cardType : messageType;
  const [results, setResults] = useState<sessionApi.MessageSearchHit[]>([]);
  const [cursor, setCursor] = useState<sessionApi.MessageSearchCursor | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const requestRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const pagingRef = useRef<number | null>(null);
  const searchKey = JSON.stringify([sessionId, query.trim(), effectiveType]);
  const latestKeyRef = useRef(searchKey);
  latestKeyRef.current = searchKey;

  useEffect(() => {
    const generation = ++generationRef.current;
    requestRef.current?.abort();
    pagingRef.current = null;
    setLoadingMore(false);
    setResults([]);
    setCursor(null);
    setHasMore(false);
    setError(false);
    const q = query.trim();
    if ((!q && effectiveType === 'all') || !sessionId) { setLoading(false); return; }
    setLoading(true);
    const timer = window.setTimeout(async () => {
      const controller = new AbortController();
      requestRef.current = controller;
      try {
        const page = await sessionApi.searchSessionMessages(sessionId, q, null, controller.signal, effectiveType);
        if (generation !== generationRef.current || searchKey !== latestKeyRef.current) return;
        setResults(page.items);
        setCursor(page.next_cursor);
        setHasMore(page.has_more);
      } catch (err) {
        if (generation === generationRef.current && searchKey === latestKeyRef.current && !controller.signal.aborted) setError(true);
      } finally {
        if (generation === generationRef.current) setLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); requestRef.current?.abort(); };
  }, [query, effectiveType, sessionId, retry, searchKey]);

  const loadMore = async () => {
    if (loading || !hasMore || !cursor || pagingRef.current !== null) return;
    const generation = generationRef.current;
    pagingRef.current = generation;
    const controller = new AbortController();
    requestRef.current = controller;
    setLoadingMore(true);
    setError(false);
    try {
      const page = await sessionApi.searchSessionMessages(sessionId, query.trim(), cursor, controller.signal, effectiveType);
      if (generation !== generationRef.current || searchKey !== latestKeyRef.current) return;
      setResults(prev => {
        const seen = new Set(prev.map(item => item.id));
        return [...prev, ...page.items.filter(item => !seen.has(item.id))];
      });
      setCursor(page.next_cursor);
      setHasMore(page.has_more);
    } catch (err) {
      if (generation === generationRef.current && searchKey === latestKeyRef.current && !controller.signal.aborted) setError(true);
    } finally {
      if (pagingRef.current === generation) pagingRef.current = null;
      if (generation === generationRef.current) setLoadingMore(false);
    }
  };

  const q = query.trim();
  const searching = Boolean(q || messageType !== 'all');
  return (
    <div className="msg-search-panel">
      <div className="msg-search-input-row">
        <span style={{ opacity: 0.6, fontSize: '0.9rem' }}>🔍</span>
        <input className="msg-search-input" type="search" value={query} autoFocus maxLength={100} aria-label="搜索消息关键词"
          placeholder="关键词，可留空按类型查找" onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Escape') onClose(); }} />
        <button className="msg-search-close" onClick={onClose} title="关闭">✕</button>
      </div>
      <div className="msg-search-types" role="group" aria-label="消息类型">
        {MESSAGE_TYPES.map(type => <button key={type.value} type="button" aria-pressed={messageType === type.value}
          onClick={() => setMessageType(type.value)}>{type.label}</button>)}
      </div>
      {messageType === 'card' && <label className="msg-search-card-filter">卡片类型
        <select aria-label="卡片类型" value={cardType} onChange={e => setCardType(e.target.value as sessionApi.MessageCardSearchType)}>
          {CARD_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}
        </select>
      </label>}
      {searching && <div className="msg-search-count" role="status">{loading ? '搜索中…' : `${results.length} 条已加载结果${hasMore ? '，可继续加载' : ''}`}</div>}
      <div className="msg-search-results">
        {!searching && <div className="msg-search-empty">输入关键词，或直接选择消息类型查找历史</div>}
        {searching && !loading && !error && results.length === 0 && <div className="msg-search-empty">{q ? `没有找到「${q}」相关${effectiveType === 'all' ? '消息' : typeLabel(effectiveType)}` : `没有找到${typeLabel(effectiveType)}消息`}</div>}
        {error && <button type="button" className="msg-search-more" onClick={() => results.length ? void loadMore() : setRetry(value => value + 1)}>加载失败，点击重试</button>}
        {results.length > 0 && <Virtuoso
          style={{ height: 'min(46vh, 420px)', maxHeight: 'calc(100dvh - 260px)' }}
          data={results}
          computeItemKey={(_, item) => item.id}
          itemContent={(_, item) => {
            const hit = q ? item.snippet.toLowerCase().indexOf(q.toLowerCase()) : -1;
            return <button type="button" className="msg-search-item" onClick={() => onLocate(item.id)}>
              <div className="msg-search-item-meta">
                <span>{item.role === 'user' ? '人类伙伴' : item.role === 'assistant' ? personaName : '事件／通知'}</span>
                <span>{formatTimestamp(new Date(item.timestamp))}</span>
              </div>
              <div className="msg-search-item-types">{item.types?.filter(type => type !== 'card' || !item.types?.some(value => value.startsWith('card_'))).map(type => <span key={type}>{typeLabel(type)}</span>)}</div>
              <div className="msg-search-item-snippet">
                {hit < 0 ? item.snippet : <>{item.snippet.slice(0, hit)}<mark>{item.snippet.slice(hit, hit + q.length)}</mark>{item.snippet.slice(hit + q.length)}</>}
              </div>
            </button>;
          }}
          components={{ Footer: () => hasMore
            ? <button type="button" className="msg-search-more" disabled={loadingMore} onClick={loadMore}>
                {loadingMore ? '加载中…' : '加载更多结果'}
              </button>
            : null }}
        />}
      </div>
    </div>
  );
}

export default MessageSearchPanel;
