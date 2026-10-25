import { useEffect, useRef, useState, type ReactNode } from 'react';

type EventRowShellProps = {
  children: ReactNode;
  canDelete: boolean;
  onDelete: () => void;
  className?: string;
};

/** Selection is local UI state; the event and its delete action remain SQL-owned. */
export default function EventRowShell({ children, canDelete, onDelete, className = '' }: EventRowShellProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState(false);

  useEffect(() => {
    if (!selected) return;
    const closeOutside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setSelected(false);
    };
    document.addEventListener('pointerdown', closeOutside, true);
    return () => document.removeEventListener('pointerdown', closeOutside, true);
  }, [selected]);

  return (
    <div
      ref={ref}
      className={`event-row-shell${className ? ` ${className}` : ''}${selected ? ' is-selected' : ''}`}
      role="group"
      aria-label="事件记录"
      tabIndex={0}
      onClick={event => {
        if (canDelete && ref.current?.contains(event.target as Node)) setSelected(true);
      }}
      onFocusCapture={() => { if (canDelete) setSelected(true); }}
      onKeyDown={event => {
        if (event.key === 'Escape') setSelected(false);
        if (canDelete && event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          setSelected(true);
        }
      }}
    >
      {children}
      {canDelete && (
        <button
          type="button"
          className="event-row-delete"
          title="删除这条事件记录"
          aria-label="删除这条事件记录"
          aria-hidden={!selected}
          tabIndex={selected ? 0 : -1}
          onClick={event => { event.stopPropagation(); onDelete(); }}
        >×</button>
      )}
    </div>
  );
}
