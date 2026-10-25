import { useEffect, useState } from 'react';

export interface ToastMessage {
  id: number;
  type: 'success' | 'error';
  message: string;
}

let _nextId = 0;
let _addToast: ((msg: Omit<ToastMessage, 'id'>) => void) | null = null;

/** 从任意组件调用，弹出 Toast 通知 */
export function showToast(message: string): void;
export function showToast(type: 'success' | 'error', message: string): void;
export function showToast(typeOrMessage: 'success' | 'error' | string, message?: string) {
  const type = message === undefined ? 'error' : typeOrMessage as 'success' | 'error';
  _addToast?.({ type, message: message ?? typeOrMessage });
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    _addToast = (msg) => {
      const id = ++_nextId;
      setToasts((prev) => [...prev.slice(-4), { ...msg, id }]); // 最多 5 条
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 4000); // 4 秒自动消失
    };
    return () => { _addToast = null; };
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className={`toast-item toast-${t.type}`}>
          <span className="toast-icon">{t.type === 'success' ? '✅' : '❌'}</span>
          <span className="toast-msg">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
