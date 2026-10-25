import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import { placePresentation, projectPlaceDisplay, type PlaceDisplay } from '../utils/userStatusLabel';

// Read existing observations only: polling never requests a phone/GPS refresh.
export function useStatusPlace() {
  const [place, setPlace] = useState<PlaceDisplay | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let active: AbortController | null = null;
    let disposed = false;
    const refresh = async () => {
      setNow(Date.now());
      if (document.visibilityState === 'hidden' || active) return;
      const controller = new AbortController();
      active = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      const read = async (path: string) => {
        const response = await fetch(`${getApiBase()}${path}`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('Location display unavailable');
        return response.json();
      };
      try {
        const [status, wifi] = await Promise.all([
          read('/api/sentinel/status'), read('/api/sentinel/wifi/status').catch(() => null),
        ]);
        if (!disposed) {
          const receivedAt = Date.now();
          setNow(receivedAt);
          setPlace(projectPlaceDisplay(status, wifi, receivedAt));
        }
      } catch {
        if (!disposed) setPlace(null);
      } finally {
        window.clearTimeout(timeout);
        if (active === controller) active = null;
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 30000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      disposed = true;
      active?.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, []);
  return placePresentation(place, now);
}
