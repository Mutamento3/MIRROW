import {useCallback, useEffect, useRef, useState} from 'react';
import {getApiBase} from '../config';
import {showToast} from '../components/Toast';
import {createSyncedMode, requestServerMode} from '../lib/syncedMode';

/** Each fixed backend mode has one controller for clicks, startup and heartbeat reads. */
export function useServerMode(cacheKey: string, endpoint: string, field: string, label: string) {
  const [value, setValue] = useState(() => localStorage.getItem(cacheKey) === 'true');
  const [pending, setPending] = useState(false);
  const ref = useRef<ReturnType<typeof createSyncedMode> | null>(null);
  if (!ref.current) {
    ref.current = createSyncedMode({
      initial: value, publish: setValue, publishPending: setPending,
      request: (method, next) => requestServerMode(`${getApiBase()}${endpoint}`, field, method, next),
      onError: () => showToast('error', `${label}切换未获确认，请核对当前状态`),
    });
  }
  const control = ref.current;
  const toggle = useCallback(async () => {await control.toggle();}, [control]);
  useEffect(() => {localStorage.setItem(cacheKey, String(value));}, [cacheKey, value]);
  useEffect(() => {void control.refresh();}, [control]);
  return {value, pending, ...control, toggle};
}
