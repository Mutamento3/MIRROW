import { useState, useEffect } from 'react';
import { getApiBase } from '../config';

// 人格演化数据 + 逻辑封装（自包含，不依赖聊天核心态）
export function usePersonaEvo() {
  const [personaSuggestions, setPersonaSuggestions] = useState<Array<any>>([]);
  const [personaEvoTab, setPersonaEvoTab] = useState<'pending'|'active'|'history'|'migrate'>('pending');
  const [activeEvolutions, setActiveEvolutions] = useState<Array<any>>([]);
  const [migratePreview, setMigratePreview] = useState<Array<any>>([]);
  const [adoptConfirm, setAdoptConfirm] = useState<{index:number;suggestion:any;conflict:any}|null>(null);
  const [historyEvolutions, setHistoryEvolutions] = useState<Array<any>>([]);
  const [historyExpandedMonths, setHistoryExpandedMonths] = useState<Set<string>>(new Set());

  const fetchPersonaSuggestions = async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/persona/suggestions`);
      const data = await res.json();
      // v2: 按 status 过滤，不再是 applied/rejected bool
      setPersonaSuggestions((data.suggestions || []).filter((s: any) =>
        s.status === 'pending' || (!s.status && !s.applied && !s.rejected)
      ));
    } catch { /* ignore */ }
  };
  const fetchActiveEvolutions = async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/persona/suggestions/active`);
      const data = await res.json();
      setActiveEvolutions(data.active || []);
    } catch { /* ignore */ }
  };
  const fetchMigratePreview = async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/persona/migrate/preview`);
      const data = await res.json();
      setMigratePreview(data.preview || []);
    } catch { /* ignore */ }
  };
  const fetchHistoryEvolutions = async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/persona/suggestions`);
      const data = await res.json();
      setHistoryEvolutions((data.suggestions || []).filter((s: any) =>
        s.status === 'rejected' || s.status === 'expired' || s.rejected
      ));
    } catch { /* ignore */ }
  };

  // 面板挂载（打开）时：拉待处理 + 活跃，重置 tab
  useEffect(() => {
    fetchPersonaSuggestions();
    fetchActiveEvolutions();
    setPersonaEvoTab('pending');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    personaSuggestions, setPersonaSuggestions, personaEvoTab, setPersonaEvoTab,
    activeEvolutions, migratePreview, setMigratePreview,
    adoptConfirm, setAdoptConfirm, historyEvolutions,
    historyExpandedMonths, setHistoryExpandedMonths,
    fetchPersonaSuggestions, fetchActiveEvolutions, fetchMigratePreview, fetchHistoryEvolutions,
  };
}
