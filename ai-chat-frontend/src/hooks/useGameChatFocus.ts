import { useEffect, useState } from 'react';
import { getApiBase } from '../config';

export function useGameChatFocus() {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const response = await fetch(`${getApiBase()}/api/wander/games/chat-focus`, {
          cache: 'no-store', signal: AbortSignal.timeout(3000),
        });
        if (response.ok) {
          const status = await response.json() as { busy: boolean };
          if (!stopped) setBusy(status.busy);
        }
      } catch { /* Backend admission remains authoritative while reconnecting. */ }
      if (!stopped) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  return busy;
}
