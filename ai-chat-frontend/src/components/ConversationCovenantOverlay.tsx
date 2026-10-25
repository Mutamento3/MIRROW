import React from 'react';

interface Props {
  countdown: string | null;
  activationNotice: boolean;
  cancelPromptOpen: boolean;
  onOpenCancelPrompt: () => void;
  onCloseCancelPrompt: () => void;
  onConfirmCancel: () => void;
}

const ConversationCovenantOverlay: React.FC<Props> = ({
  countdown,
  activationNotice,
  cancelPromptOpen,
  onOpenCancelPrompt,
  onCloseCancelPrompt,
  onConfirmCancel,
}) => (
  <>
    {countdown !== null && (
      <button
        type="button"
        className="covenant-countdown"
        onClick={onOpenCancelPrompt}
        title="点击可取消自动结束"
      >
        {countdown}
      </button>
    )}

    {activationNotice && (
      <div className="covenant-activation-notice" role="status">
        约定已生效，30:00 后自动聊到这里
      </div>
    )}

    {cancelPromptOpen && (
      <div className="covenant-cancel-overlay" onClick={onCloseCancelPrompt}>
        <div className="covenant-cancel-dialog" role="dialog" aria-modal="true" aria-labelledby="covenant-cancel-title" onClick={event => event.stopPropagation()}>
          <div id="covenant-cancel-title" className="covenant-cancel-title">取消这次倒计时？</div>
          <p>取消后，本次不会自动结束聊天。之后的新消息仍可再次触发约定。</p>
          <div className="covenant-cancel-actions">
            <button type="button" className="btn-secondary" onClick={onCloseCancelPrompt}>继续倒计时</button>
            <button type="button" className="btn-primary" onClick={onConfirmCancel}>确认取消</button>
          </div>
        </div>
      </div>
    )}
  </>
);

export default ConversationCovenantOverlay;
