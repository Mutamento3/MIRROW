import { useEffect, useRef, useState } from 'react';

export default function CognitionMenu({ items }: { items: { label: string; action: () => void }[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  return <div className="reading-menu" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button ref={trigger} aria-expanded={open} onClick={() => setOpen(!open)}>更多 ···</button>
    {open && <div className="reading-menu-items">{items.map(item => <button key={item.label} onClick={() => { setOpen(false); trigger.current?.focus(); item.action(); }}>{item.label}</button>)}</div>}
  </div>;
}
