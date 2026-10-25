import { useState, useEffect } from 'react';
import { getApiBase } from '../config';

const LEDGER_PAGE_SIZE = 15;

// 记账本数据 + 逻辑封装（自包含，不依赖聊天核心态）
export function useLedger() {
  const [ledgerEntries, setLedgerEntries] = useState<Array<any>>([]);
  const [ledgerPage, setLedgerPage] = useState(0);
  const [ledgerTotal, setLedgerTotal] = useState(0);
  const [ledgerFilter, setLedgerFilter] = useState<string>('');
  const [disputingEntry, setDisputingEntry] = useState<string | null>(null);
  const [disputeComment, setDisputeComment] = useState('');
  const [detailEntry, setDetailEntry] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 计算条目距自动变 dormant 的剩余天数
  const getExpiryDays = (e: any): number | null => {
    if (!e.last_active) return null;
    const multiplier: Record<string,number> = { wander_check: 0.5, serious_flirt: 1.0, promise_broken: 1.5, hidden_truth: 1.5 };
    const threshold = 14 * (multiplier[e.debt_type] || 1.0);
    const daysSince = (Date.now() - new Date(e.last_active).getTime()) / 86400000;
    return Math.max(0, Math.ceil(threshold - daysSince));
  };

  const fetchLedger = async (page: number = 0, filterType: string = '') => {
    try {
      const ft = filterType || ledgerFilter;
      const offset = page * LEDGER_PAGE_SIZE;
      const typeParam = ft ? `&debt_type=${encodeURIComponent(ft)}` : '';
      const res = await fetch(`${getApiBase()}/api/ledger/entries?limit=${LEDGER_PAGE_SIZE}&offset=${offset}${typeParam}`);
      const data = await res.json();
      setLedgerEntries(data.entries || []);
      setLedgerTotal(data.total || 0);
    } catch (e) { console.error('获取账本失败:', e); }
  };
  const handleDeleteLedger = async (entryId: string) => {
    try {
      const res = await fetch(`${getApiBase()}/api/ledger/entries/${entryId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      fetchLedger(ledgerPage);
    } catch { /* ignore */ }
  };
  const handleSettleLedger = async (entryId: string) => {
    try {
      const res = await fetch(`${getApiBase()}/api/ledger/entries/${entryId}/settle`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      fetchLedger(ledgerPage);
    } catch { /* ignore */ }
  };
  const handleDisputeLedger = async (entryId: string) => {
    try {
      await fetch(`${getApiBase()}/api/ledger/entries/${entryId}/comment`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comment: disputeComment }),
      });
      setLedgerEntries(prev => prev.map(e => e.id === entryId ? { ...e, comment: disputeComment } : e));
      setDisputingEntry(null);
      setDisputeComment('');
    } catch { /* ignore */ }
  };
  const fetchDetail = async (entryId: string) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`${getApiBase()}/api/ledger/entries/${entryId}/detail`);
      const data = await res.json();
      setDetailEntry(data);
    } catch { setDetailEntry(null); }
    setDetailLoading(false);
  };

  // 面板挂载（打开）时：重置分页 + 拉第一页
  useEffect(() => {
    setLedgerPage(0);
    fetchLedger(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    ledgerEntries, ledgerPage, setLedgerPage, ledgerTotal, ledgerFilter, setLedgerFilter,
    disputingEntry, setDisputingEntry, disputeComment, setDisputeComment,
    detailEntry, setDetailEntry, detailLoading,
    getExpiryDays, fetchLedger, handleDeleteLedger, handleSettleLedger, handleDisputeLedger, fetchDetail,
    LEDGER_PAGE_SIZE,
  };
}
