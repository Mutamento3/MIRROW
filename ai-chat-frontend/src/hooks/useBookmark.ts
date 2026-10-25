import { useState, useEffect } from 'react';
import { getApiBase } from '../config';
import { useChat } from '../context/chat';
import type { Message } from '../types';

const BOOKMARK_PAGE_SIZE = 10;

// 收藏（书签）数据 + 逻辑封装。⚠️ bookmarkedIds / handleBookmarkMessage 被「消息列表的⭐按钮」
// 和「收藏面板」共享，所以本 hook 必须由 App 调用一次，把整个返回值作为单个 prop 传给 BookmarkPanel，
// 不能各自调用 useBookmark()（会产生两份独立 state 导致⭐状态不同步）。
export function useBookmark() {
  const { currentSessionId } = useChat();
  const [bookmarkedIds, setBookmarkedIds] = useState<Set<string>>(new Set());
  const [bookmarkBucketMap, setBookmarkBucketMap] = useState<Record<string,string>>({});
  const [bookmarkList, setBookmarkList] = useState<Array<any>>([]);
  const [bookmarkTab, setBookmarkTab] = useState<'k'|'user'>('user');
  const [bookmarkPage, setBookmarkPage] = useState(0);
  const [bookmarkTotal, setBookmarkTotal] = useState(0);
  const [expandedBookmarkContent, setExpandedBookmarkContent] = useState<Set<string>>(new Set());
  const [bookmarkContextWindow, setBookmarkContextWindow] = useState(() => parseInt(localStorage.getItem('bookmarkContextWindow') || '3'));
  const [contextPopup, setContextPopup] = useState<{bookmarkId:string;bookmarkContent:string;before:number;after:number;data:{before:any[];target:any;after:any[];hasMoreBefore:boolean;hasMoreAfter:boolean}|null;showRangeMenu:boolean}|null>(null);

  const fetchBookmarks = async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/bookmarks?collected_by=user&limit=2000`);
      const data = await res.json();
      const ids = new Set<string>();
      const map: Record<string,string> = {};
      for (const b of (data.bookmarks || [])) {
        if (b.original_msg_id) { ids.add(b.original_msg_id); map[b.original_msg_id] = b.id; }
      }
      setBookmarkedIds(ids);
      setBookmarkBucketMap(map);
    } catch { /* ignore */ }
  };

  const fetchBookmarkList = async (collectedBy?: string, page?: number) => {
    try {
      const cb = collectedBy ?? bookmarkTab;
      const pg = page ?? bookmarkPage;
      const res = await fetch(`${getApiBase()}/api/bookmarks?collected_by=${cb}&offset=${pg * BOOKMARK_PAGE_SIZE}&limit=${BOOKMARK_PAGE_SIZE}`);
      const data = await res.json();
      setBookmarkList(data.bookmarks || []);
      setBookmarkTotal(data.total || 0);
      // 注意：不在此处更新 bookmarkedIds/bookmarkBucketMap（分页数据只有当前页 10 条）
      // 全局星标追踪由 mount 时的 fetchBookmarks(limit=2000) 负责
    } catch { /* ignore */ }
  };

  const handleBookmarkMessage = async (message: Message) => {
    const msgId = message.id;
    if (bookmarkedIds.has(msgId)) {
      // 取消收藏 — 乐观更新
      const bucketId = bookmarkBucketMap[msgId];
      setBookmarkedIds(prev => { const n = new Set(prev); n.delete(msgId); return n; });
      if (bucketId) {
        try { await fetch(`${getApiBase()}/api/bookmarks/` + encodeURIComponent(bucketId), { method: 'DELETE' }); } catch {
          setBookmarkedIds(prev => { const n = new Set(prev); n.add(msgId); return n; });
          setBookmarkBucketMap(prev => { const m = {...prev}; m[msgId] = bucketId; return m; });
        }
      }
    } else {
      // 添加收藏 — 乐观更新
      setBookmarkedIds(prev => { const n = new Set(prev); n.add(msgId); return n; });
      let content = message.content || '';
      try {
        const res = await fetch(`${getApiBase()}/api/bookmarks/` + encodeURIComponent(msgId), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content,
            role: message.role,
            session_id: currentSessionId,
            timestamp: message.timestamp,
            collected_by: 'user',
          }),
        });
        const data = await res.json();
        if (data.bucket_id) {
          setBookmarkBucketMap(prev => ({ ...prev, [msgId]: data.bucket_id }));
        }
      } catch { setBookmarkedIds(prev => { const n = new Set(prev); n.delete(msgId); return n; }); }
    }
  };

  // 挂载时拉全量收藏，初始化⭐状态
  useEffect(() => { fetchBookmarks(); /* eslint-disable-line */ }, []);

  // 打开收藏夹面板时：切到「我的收藏」+ 重置分页 + 拉第一页
  const openPanel = () => {
    setBookmarkTab('user');
    setBookmarkPage(0);
    fetchBookmarkList('user', 0);
  };

  return {
    bookmarkedIds, setBookmarkedIds, bookmarkBucketMap, setBookmarkBucketMap,
    bookmarkList, setBookmarkList, bookmarkTab, setBookmarkTab,
    bookmarkPage, setBookmarkPage, bookmarkTotal, setBookmarkTotal,
    expandedBookmarkContent, setExpandedBookmarkContent,
    bookmarkContextWindow, setBookmarkContextWindow,
    contextPopup, setContextPopup,
    fetchBookmarkList, handleBookmarkMessage, openPanel, BOOKMARK_PAGE_SIZE,
  };
}
