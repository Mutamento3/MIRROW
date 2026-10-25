import type { ToolCallRecord } from '../types';
import './BandEventNotice.css';
import { hiddenBandReceipt } from '../lib/chatReceipts';

export default function BandEventNotice({ call, independent = false }: { call: ToolCallRecord; independent?: boolean }) {
  if (!independent && hiddenBandReceipt(call)) return null;
  const params = call.parameters || {};
  const note = typeof params.note === 'string' && params.note.trim().length > 0;
  const success = call.success === true || (call.success !== false && call.status === 'success');
  const status = ['rejected', 'skipped', 'blocked'].includes(call.status || '') ? '未执行'
    : call.success === false ? '失败或结果未确认' : '等待回执';
  if (success) return <div className="band-event-notice">{note
    ? `✉️ AI 发来一张小纸条：${typeof params.note === 'string' ? params.note : '（未记录正文）'}`
    : '⌚ AI 震了手环一下'}</div>;
  return <details className="band-event-notice">
    <summary>{note ? '✉️ 手环纸条' : '⌚ 手环震动'} · {status} · 错误详情</summary>
    <div className="band-event-detail">
      {note && <><strong>当时提交的纸条</strong><p>{typeof params.note === 'string' ? params.note : '未记录正文'}</p></>}
      <p>{call.result || call.error || status}</p>
    </div>
  </details>;
}
