/** 会话标题编辑组件 */
import { useState } from 'react';

export function SessionTitleEditor({ initialTitle, sessionId, onSave, onCancel }: {
  initialTitle: string;
  sessionId: string;
  onSave: (sessionId: string, newTitle: string) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(initialTitle);

  const handleSave = () => {
    if (title.trim() && title !== initialTitle) {
      onSave(sessionId, title);
    } else {
      onCancel();
    }
  };

  return (
    <div className="session-title-edit">
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            handleSave();
          } else if (e.key === 'Escape') {
            onCancel();
          }
        }}
        onBlur={handleSave}
        autoFocus
        className="session-title-input"
      />
    </div>
  );
}
