/** 可拖拽通知卡片组件 */
import { useState, useEffect, useRef } from 'react';

export function DraggableCard({ title, message, initialX, initialY, onClose }: {
  title: string; message: string; initialX: number; initialY: number; onClose: () => void;
}) {
  const [pos, setPos] = useState({ x: initialX, y: initialY });
  const [dragging, setDragging] = useState(false);
  const dragOffset = useRef({ x: 0, y: 0 });
  const cardRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.card-close-btn')) return;
    setDragging(true);
    dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
    e.preventDefault();
  };

  useEffect(() => {
    if (!dragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      setPos({ x: e.clientX - dragOffset.current.x, y: e.clientY - dragOffset.current.y });
    };
    const handleMouseUp = () => setDragging(false);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [dragging]);

  return (
    <div
      ref={cardRef}
      className={`notification-card${dragging ? ' dragging' : ''}`}
      style={{ left: pos.x, top: pos.y }}
      onMouseDown={handleMouseDown}
    >
      <div className="card-header">
        <span className="card-title">⏰ {title}</span>
        <button className="card-close-btn" onClick={onClose}>×</button>
      </div>
      <div className="card-body">{message}</div>
    </div>
  );
}
