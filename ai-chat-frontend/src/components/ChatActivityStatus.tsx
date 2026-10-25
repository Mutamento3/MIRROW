import './ChatActivityStatus.css';

/** Transient activity is composer chrome, not a message in AI's history. */
export default function ChatActivityStatus({ label, background = false }: { label?: string; background?: boolean }) {
  if (!label) return null;
  return <div className="chat-activity-status" role="status" aria-live="polite" aria-atomic="true" title={label}>
    <span className={`chat-activity-dot${background ? ' background' : ''}`} aria-hidden="true" />
    <span className="chat-activity-label">{label}</span>
  </div>;
}
