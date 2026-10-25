import {useRef, useState} from 'react';
import type {Message} from '../types';
import {ResendRequestError} from '../lib/messageResend';

/** One active edit form, owned above the virtualized message list. */
export type MessageEditDraft = {messageId: string; content: string; replacementId?: string; unconfirmed?: boolean; pending?: boolean; error?: string};

type MessageEditAreaProps = {
  initialContent: string;
  draft?: MessageEditDraft;
  message: Message;
  isLoading: boolean;
  onSave: (messageId: string, content: string) => void;
  onResend: (messageId: string, content: string, replacementId: string) => void | Promise<void>;
  onCancel: () => void;
};

export default function MessageEditArea({ initialContent, draft: suppliedDraft, message, isLoading, onSave, onResend, onCancel }: MessageEditAreaProps) {
  const fallback = useRef<MessageEditDraft>({messageId: message.id, content: initialContent});
  const draft = suppliedDraft || fallback.current;
  const [, setVersion] = useState(0);
  const render = () => setVersion(value => value + 1);
  const {content, pending, unconfirmed, error} = draft;
  const submitting = useRef(false);
  const resend = async () => {
    if (submitting.current || draft.pending || isLoading || !content.trim()) return;
    submitting.current = true; draft.pending = true; draft.error = ''; render();
    draft.replacementId ||= `${Date.now()}-resend-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`;
    try { await onResend(message.id, content, draft.replacementId); }
    catch (cause) {
      const unknown = cause instanceof ResendRequestError && cause.unconfirmed;
      draft.unconfirmed = unknown;
      if (!unknown) draft.replacementId = undefined;
      draft.error = cause instanceof Error ? cause.message : '重发未完成，编辑稿已保留';
    } finally { draft.pending = false; submitting.current = false; render(); }
  };

  return (
    <div className="message-edit-container">
      <textarea
        className="message-edit-input"
        value={content}
        readOnly={pending || unconfirmed}
        onChange={(e) => { draft.content = e.target.value; render(); }}
        rows={4}
      />
      <div className="message-edit-buttons">
        {message.role === 'user' && (
          <button
            className="message-edit-resend"
            onClick={() => void resend()}
            disabled={isLoading || pending || !content.trim()}
            title="修改后重新发送，覆盖之后的AI回复"
          >
            {pending ? '正在确认…' : unconfirmed ? '确认重发结果' : '重发'}
          </button>
        )}
        <button
          className="message-edit-save"
          disabled={pending || unconfirmed || isLoading}
          onClick={() => onSave(message.id, content)}
        >
          保存
        </button>
        <button
          className="message-edit-cancel"
          disabled={pending || unconfirmed || isLoading}
          onClick={onCancel}
        >
          取消
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
