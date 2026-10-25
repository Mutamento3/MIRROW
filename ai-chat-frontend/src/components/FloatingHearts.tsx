/** 纯展示组件：深夜模式爱心漂浮动画 */
import { useMemo } from 'react';

const HEARTS = ['❤️', '💕', '💗', '💖', '🩷'];

export function FloatingHearts() {
  const hearts = useMemo(() =>
    Array.from({ length: 10 }, (_, i) => ({
      key: i,
      left: 5 + Math.random() * 90,
      delay: Math.random() * 8,
      duration: 6 + Math.random() * 10,
      size: 0.8 + Math.random() * 1.6,
      emoji: HEARTS[i % 5],
    })), []);
  return (
    <div className="heart-float-container" aria-hidden="true">
      {hearts.map(h => (
        <span
          key={h.key}
          className="heart-float"
          style={{
            left: `${h.left}%`,
            animationDelay: `${h.delay}s`,
            animationDuration: `${h.duration}s`,
            fontSize: `${h.size}rem`,
          }}
        >
          {h.emoji}
        </span>
      ))}
    </div>
  );
}
