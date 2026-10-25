import * as sessionApi from '../sessionApi';

const DB_NAME = 'mirrow-session-history-v1';
const PAGE_SIZE = 200;
const PAGE_DELAY_MS = 180;

type CacheMeta = {
  sessionId: string;
  revision: string;
  total: number;
  loaded: number;
  complete: boolean;
  nextId: string;
  nextTs: string;
  nextRowId: number;
};

type CachedPage = { key: string; sessionId: string; page: sessionApi.ApiSession };
export type FullHistoryProgress = Pick<CacheMeta, 'revision' | 'loaded' | 'total' | 'complete'>;

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 2);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('meta')) {
          request.result.createObjectStore('meta', { keyPath: 'sessionId' });
        }
        const pages = request.result.objectStoreNames.contains('pages')
          ? request.transaction!.objectStore('pages')
          : request.result.createObjectStore('pages', { keyPath: 'key' });
        if (!pages.indexNames.contains('sessionId')) pages.createIndex('sessionId', 'sessionId');
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    }).catch(error => {
      dbPromise = null;
      throw error;
    });
  }
  return dbPromise!;
}

function pageKey(sessionId: string, beforeId: string): string {
  return JSON.stringify([sessionId, beforeId || '__tail__']);
}

async function readStore<T>(storeName: string, key: string): Promise<T | undefined> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction(storeName, 'readonly').objectStore(storeName).get(key);
    request.onsuccess = () => resolve(request.result as T | undefined);
    request.onerror = () => reject(request.error);
  });
}

async function writeMeta(meta: CacheMeta, beforeId?: string, page?: sessionApi.ApiSession): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['meta', 'pages'], 'readwrite');
    transaction.objectStore('meta').put(meta);
    if (page) transaction.objectStore('pages').put({
      key: pageKey(meta.sessionId, beforeId || ''), sessionId: meta.sessionId, page,
    });
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function clearSessionPages(sessionId: string): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('pages', 'readwrite');
    const pages = transaction.objectStore('pages');
    const cursor = pages.index('sessionId').openKeyCursor(IDBKeyRange.only(sessionId));
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (row) { pages.delete(row.primaryKey); row.continue(); }
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export async function getFullHistoryProgress(sessionId: string): Promise<FullHistoryProgress | null> {
  const meta = await readStore<CacheMeta>('meta', sessionId);
  return meta ? { revision: meta.revision, loaded: meta.loaded, total: meta.total, complete: meta.complete } : null;
}

/** 主聊天向上翻页时只读已完成且仍匹配服务端版本的本机缓存。 */
export async function getCachedHistoryPage(sessionId: string, beforeId: string): Promise<sessionApi.ApiSession | null> {
  const meta = await readStore<CacheMeta>('meta', sessionId).catch(() => undefined);
  if (!meta?.complete) return null;
  try {
    const current = await sessionApi.getSessionMessageCount(sessionId);
    if (current.updatedAt !== meta.revision || current.total !== meta.total) return null;
    const cached = await readStore<CachedPage>('pages', pageKey(sessionId, beforeId));
    return cached?.page || null;
  } catch {
    return null;
  }
}

export async function invalidateFullHistoryCache(sessionId: string): Promise<void> {
  const meta = await readStore<CacheMeta>('meta', sessionId).catch(() => undefined);
  if (meta) await writeMeta({ ...meta, complete: false });
}

/** 每页最多 200 条，逐页写入当前设备的 IndexedDB；不占用 React 聊天消息数组。 */
export async function prefetchFullHistory(
  sessionId: string,
  onProgress: (progress: FullHistoryProgress) => void,
  signal: AbortSignal,
  shouldPause: () => boolean,
): Promise<FullHistoryProgress> {
  const snapshot = await sessionApi.getSessionMessageCount(sessionId, signal);
  let meta = await readStore<CacheMeta>('meta', sessionId);
  if (!meta || meta.revision !== snapshot.updatedAt || meta.total !== snapshot.total) {
    await clearSessionPages(sessionId);
    meta = { sessionId, revision: snapshot.updatedAt, total: snapshot.total, loaded: 0,
      complete: false, nextId: '', nextTs: '', nextRowId: 0 };
    await writeMeta(meta);
  }
  onProgress(meta);
  if (meta.complete) return meta;

  let hasMore = true;
  while (hasMore) {
    if (signal.aborted) throw new DOMException('已取消预读', 'AbortError');
    while (shouldPause()) {
      if (signal.aborted) throw new DOMException('已取消预读', 'AbortError');
      await new Promise(resolve => window.setTimeout(resolve, 300));
    }
    const beforeId = meta.nextId;
    const page = await sessionApi.getSession(sessionId, {
      limit: PAGE_SIZE,
      beforeId: beforeId || undefined,
      beforeTs: meta.nextTs || undefined,
      beforeRowId: meta.nextRowId || undefined,
    });
    if (signal.aborted) throw new DOMException('已取消预读', 'AbortError');
    if (!page || page.cursorStale || (page.hasMore && !page.messages.length)) {
      throw new Error('历史游标已变化，稍后可重新预读');
    }
    meta = {
      ...meta,
      loaded: meta.loaded + page.messages.length,
      nextId: page.oldestId || '',
      nextTs: page.oldestTs || '',
      nextRowId: page.oldestRowId || 0,
    };
    await writeMeta(meta, beforeId, page);
    onProgress(meta);
    hasMore = !!page.hasMore;
    if (hasMore) await new Promise(resolve => window.setTimeout(resolve, PAGE_DELAY_MS));
  }
  const current = await sessionApi.getSessionMessageCount(sessionId, signal);
  if (signal.aborted) throw new DOMException('已取消预读', 'AbortError');
  if (current.updatedAt !== snapshot.updatedAt || current.total !== snapshot.total) {
    throw new Error('预读期间消息发生变化，请点击按钮重新续载');
  }
  meta = { ...meta, complete: true };
  await writeMeta(meta);
  onProgress(meta);
  return meta;
}
