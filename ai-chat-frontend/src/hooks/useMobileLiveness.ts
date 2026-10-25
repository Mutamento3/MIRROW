import { useCallback, useEffect, useState } from 'react';
import { getApiBase } from '../config';

export interface MobileRelayConnection {
  connection_id: string;
  client_kind: string;
  connected_at: number;
  last_rx_at: number;
  last_heartbeat_at: number;
  last_tool_result_at?: number | null;
  heartbeat_age_seconds?: number | null;
  capabilities: string[];
}

export interface MobileLiveness {
  state: 'online' | 'degraded' | 'offline' | 'unknown' | string;
  native_online: boolean;
  native_count: number;
  webview_count: number;
  online_lease_seconds: number;
  stale_lease_seconds: number;
  last_disconnect_at?: number | null;
  last_disconnect_reason?: string | null;
  connections: MobileRelayConnection[];
  delivery_outbox?: Record<string, number>;
}

export function useMobileLiveness(enabled = true) {
  const [status, setStatus] = useState<MobileLiveness | null>(null);
  const [loading, setLoading] = useState(true);

  const refetch = useCallback(async () => {
    try {
      const response = await fetch(`${getApiBase()}/api/debug/mobile-liveness`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setStatus(await response.json());
    } catch {
      // 监控卡片只反映当前能确认的事实，接口失败不伪造 offline。
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    refetch();
    const timer = window.setInterval(refetch, 30000);
    return () => window.clearInterval(timer);
  }, [enabled, refetch]);

  return { status, loading, refetch };
}
