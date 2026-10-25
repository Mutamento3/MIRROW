import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';

export interface MainChatModelProfile {
  id: string;
  label: string;
  model: string;
  supports_vision: boolean;
  available: boolean;
  missing_env: string[];
  group_id?: string;
  group_label?: string;
  source_label?: string;
  billing_label?: string;
}

interface MainChatModelState {
  active_profile_id: string;
  profiles: MainChatModelProfile[];
}

const MAIN_MODEL_CHANGED_EVENT = 'mirrow-main-chat-model-changed';

export function useMainChatModel() {
  const [state, setState] = useState<MainChatModelState | null>(null);
  const [switching, setSwitching] = useState(false);

  const refresh = useCallback(async () => {
    const response = await fetch(`${getApiBase()}/api/config/main-chat-model`, { cache: 'no-store' });
    if (!response.ok) throw new Error('读取主聊天模型失败');
    setState(await response.json() as MainChatModelState);
  }, []);

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<MainChatModelState>).detail;
      if (detail?.active_profile_id && Array.isArray(detail.profiles)) {
        setState(detail);
      } else {
        void refresh().catch(() => undefined);
      }
    };
    window.addEventListener(MAIN_MODEL_CHANGED_EVENT, sync);
    return () => window.removeEventListener(MAIN_MODEL_CHANGED_EVENT, sync);
  }, [refresh]);

  const switchProfile = useCallback(async (profileId: string) => {
    setSwitching(true);
    try {
      const response = await fetch(`${getApiBase()}/api/config/main-chat-model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || '切换主聊天模型失败');
      setState(body as MainChatModelState);
      window.dispatchEvent(new CustomEvent(MAIN_MODEL_CHANGED_EVENT, { detail: body }));
      return body as MainChatModelState;
    } finally {
      setSwitching(false);
    }
  }, []);

  return { state, switching, switchProfile };
}
