import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';

export type MemoryTab = 'memories' | 'archive' | 'timeline';
export const MEMORY_PAGE_SIZE = 12;
export interface MemoryCard {
  id: string; kind: string; name?: string; summary: string; date_from?: string;
  date_to?: string; event_count?: number; source_count?: number;
  summary_truncated?: boolean; source: string;
}
export async function readMemory<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${getApiBase()}/api/memory-v2/library/${path}`, { signal, cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '记忆读取失败，请稍后重试');
  }
  return response.json();
}

export function useMemory() {
  const [memoryTab, setMemoryTab] = useState<MemoryTab>('memories');
  const [memories, setMemories] = useState<MemoryCard[]>([]);
  const [memoryLoading, setMemoryLoading] = useState(true);
  const [memoryError, setMemoryError] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [revision, setRevision] = useState(0);
  const [partial, setPartial] = useState(false);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const changeTab = (tab: MemoryTab) => { setOffset(0); setMemoryTab(tab); };
  const search = (value: string) => { setOffset(0); setQuery(value); };
  useEffect(() => {
    if (memoryTab === 'timeline') return;
    const controller = new AbortController();
    setMemoryLoading(true); setMemoryError(''); setMemories([]); setPartial(false);
    const endpoint = memoryTab === 'archive' ? 'archive' : 'memories';
    readMemory<{ memories: MemoryCard[]; total: number; complete?: boolean }>(
      `${endpoint}?offset=${offset}&limit=${MEMORY_PAGE_SIZE}&query=${encodeURIComponent(query)}`, controller.signal,
    ).then(data => {
      if (controller.signal.aborted) return;
      setMemories(data.memories); setTotal(data.total); setPartial(data.complete === false);
      if (offset > 0 && offset >= data.total) setOffset(Math.max(0, (Math.ceil(data.total / MEMORY_PAGE_SIZE) - 1) * MEMORY_PAGE_SIZE));
    }).catch(error => {
      if (!controller.signal.aborted) setMemoryError(error instanceof Error ? error.message : '记忆读取失败');
    }).finally(() => { if (!controller.signal.aborted) setMemoryLoading(false); });
    return () => controller.abort();
  }, [memoryTab, offset, query, revision]);
  return { memoryTab, changeTab, memories, memoryLoading, memoryError, offset,
    setOffset, total, search, refresh, partial, query };
}
