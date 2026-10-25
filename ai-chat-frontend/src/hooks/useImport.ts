import { useState } from 'react';
import type { ChangeEvent } from 'react';
import { getApiBase } from '../config';
import * as sessionApi from '../sessionApi';
import { useChat } from '../context/chat';

// 消息导入数据 + 逻辑封装（依赖聊天核心态的 reloadCurrentSession / currentSessionId）
export function useImport() {
  const { currentSessionId, setCurrentSessionId, reloadCurrentSession } = useChat();
  const [importStep, setImportStep] = useState<'upload'|'dates'|'confirm'|'progress'|'done'>('upload');
  const [importId, setImportId] = useState<string | null>(null);
  const [importDates, setImportDates] = useState<Array<{date:string; message_count:number; already_exists:boolean}>>([]);
  const [importSelectedDates, setImportSelectedDates] = useState<Set<string>>(new Set());
  const [importProgress, setImportProgress] = useState({ current: 0, total: 0 });
  const [importResult, setImportResult] = useState<{imported_messages:number; imported_bookmarks:number; session_id:string; title:string} | null>(null);
  const [importTargetSession, setImportTargetSession] = useState<'current'|'new'>('new');

  const handleImportFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.db')) {
      alert('仅支持 .db SQLite 文件');
      return;
    }

    setImportStep('progress');
    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch(`${getApiBase()}/api/import/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json();
        alert('上传失败: ' + (err.detail || res.statusText));
        setImportStep('upload');
        return;
      }
      const data = await res.json();
      setImportId(data.import_id);
      setImportDates(data.available_dates || []);
      setImportSelectedDates(new Set());
      setImportStep('dates');
    } catch (err) {
      alert('上传失败: ' + String(err));
      setImportStep('upload');
    }
  };

  const toggleImportDate = (date: string) => {
    setImportSelectedDates(prev => {
      const next = new Set(prev);
      if (next.has(date)) { next.delete(date); } else { next.add(date); }
      return next;
    });
  };

  const selectAllImportDates = () => {
    if (importSelectedDates.size === importDates.length) {
      setImportSelectedDates(new Set());
    } else {
      setImportSelectedDates(new Set(importDates.map(d => d.date)));
    }
  };

  const startImportConfirm = () => {
    if (importSelectedDates.size === 0) {
      alert('请至少选择一个日期');
      return;
    }
    setImportStep('confirm');
  };

  const executeImport = async () => {
    if (!importId || importSelectedDates.size === 0) return;

    setImportStep('progress');
    setImportProgress({ current: 0, total: importSelectedDates.size });

    try {
      const body: any = { import_id: importId, dates: Array.from(importSelectedDates).sort() };
      if (importTargetSession === 'current') {
        body.session_id = currentSessionId;
      }

      const res = await fetch(`${getApiBase()}/api/import/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json();
        alert('导入失败: ' + (err.detail || res.statusText));
        setImportStep('dates');
        return;
      }
      const data = await res.json();
      setImportResult(data);
      setImportProgress({ current: importSelectedDates.size, total: importSelectedDates.size });
      setImportStep('done');

      // 刷新会话列表
      if (data.session_id) {
        if (importTargetSession === 'new') {
          await sessionApi.setCurrentSession(data.session_id);
          setCurrentSessionId(data.session_id);
        }
        reloadCurrentSession();
        // 延迟再刷新一次确保数据同步
        setTimeout(() => reloadCurrentSession(), 500);
      }
    } catch (err) {
      alert('导入失败: ' + String(err));
      setImportStep('dates');
    }
  };

  const getSelectedDatesCount = () => {
    return importDates
      .filter(d => importSelectedDates.has(d.date))
      .reduce((sum, d) => sum + d.message_count, 0);
  };

  const resetImport = () => { setImportStep('upload'); };

  return {
    importStep, importId, importDates, importSelectedDates,
    importProgress, importResult, importTargetSession, setImportTargetSession,
    handleImportFile, toggleImportDate, selectAllImportDates,
    startImportConfirm, executeImport, getSelectedDatesCount, resetImport,
  };
}
