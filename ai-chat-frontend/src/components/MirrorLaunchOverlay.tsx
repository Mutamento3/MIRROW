import { useEffect, useRef, useState } from 'react';
import './MirrorLaunchOverlay.css';

const LAUNCH_SEEN_KEY = 'mirrow.web.launch-overlay-seen';
let launchClaimedInRuntime = false;

function wasAlreadyShown(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.sessionStorage.getItem(LAUNCH_SEEN_KEY) === '1';
  } catch {
    return false;
  }
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
}

function markShown(): void {
  try {
    window.sessionStorage.setItem(LAUNCH_SEEN_KEY, '1');
  } catch {
    // The runtime claim still prevents a duplicate veil in this WebView.
  }
}

/**
 * First-load veil shared by desktop and the Capacitor WebView. The visual is
 * intentionally abstract: a reflection wakes, the name resolves, and the
 * dark veil releases the already-rendered interface underneath.
 */
export default function MirrorLaunchOverlay() {
  const [visible, setVisible] = useState(() => !wasAlreadyShown());
  const timerStarted = useRef(false);

  useEffect(() => {
    if (!visible || launchClaimedInRuntime) return;
    launchClaimedInRuntime = true;
    markShown();
    if (timerStarted.current) return;
    timerStarted.current = true;

    const duration = prefersReducedMotion() ? 600 : 2100;
    window.setTimeout(() => setVisible(false), duration);
  }, [visible]);

  if (!visible) return null;

  return (
    <div className="awakening-overlay" aria-hidden="true">
      <div className="awakening-aurora awakening-aurora-left" />
      <div className="awakening-aurora awakening-aurora-right" />
      <div className="awakening-center">
        <div className="awakening-reflection">
          <span className="awakening-core" />
          <span className="awakening-haze" />
        </div>
        <div className="awakening-wordmark">MIRROW</div>
        <div className="awakening-thread" />
      </div>
    </div>
  );
}
