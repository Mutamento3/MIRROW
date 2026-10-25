import { useState, useEffect, useCallback } from 'react';
import { getApiBase } from '../config';

export interface SentinelStatus {
  running: boolean;
  enabled: boolean;
  tick_count: number;
  tick_interval: number;
  sources: string[];
  source_states: Record<string, string>;
  source_errors?: Record<string, string>;
  source_contracts?: Record<string, {
    kind?: string; time_basis?: string; nominal_max_age_seconds?: number | null;
    proves?: string; does_not_prove?: string;
  }>;
  source_freshness?: Record<string, {
    state?: 'fresh' | 'stale' | 'persistent' | 'unknown';
    captured_at?: string | null; age_seconds?: number | null;
    max_age_seconds?: number | null; field_key?: string;
  }>;
  baseline_diagnostics?: {
    confidence?: number;
    dimensions?: Record<string, { available?: boolean; sample_days?: number; summary?: string }>;
  };
  last_snapshot?: {
    observed_at?: string | null;
    heart_rate: number | null; input_idle_seconds: number | null;
    physical_input_idle_seconds?: number | null; remote_input_idle_seconds?: number | null;
    input_origin_available?: boolean; last_input_origin?: string | null;
    screen_active: boolean | null; foreground_app: string | null;
    mirrow_visible: boolean | null; user_status: string | null;
    user_status_since?: string | null;
    semantic_observations?: Array<{
      key?: string; state?: string; text?: string; observed_at?: string;
      confidence?: string; attention_eligible?: boolean;
      evidence?: Array<Record<string, unknown>>;
    }>;
    is_period: boolean | null; period_day: number | null;
    steps_today: number | null; steps_stagnant_minutes: number;
    location_address: string | null; location_category: string | null;
    location_place_id?: string | null; location_place_name?: string | null;
    location_environment?: 'indoor' | 'outdoor' | 'unknown' | null;
    location_label?: string;
    behavior_state?: string | null; screen_time_minutes?: number | null;
    screen_time_logical_date?: string | null;
    top_app_category?: string | null; mobile_app_package?: string | null;
    mobile_app_name?: string | null; mobile_screen_on?: boolean | null;
    mobile_observed_window_package?: string | null; mobile_overlay_package?: string | null;
    mobile_underlying_package?: string | null; mobile_foreground_resolution?: string | null;
    phone_posture?: string | null; phone_motion?: string | null; phone_light?: string | null;
    remote_control_active?: boolean | null;
    weather_temp?: number | null; weather_humidity?: number | null; weather_desc?: string | null;
    weather_code?: number | null; weather_cloudcover?: number | null; weather_uv_index?: number | null;
    weather_wind_speed?: number | null; weather_wind_dir?: string | null;
    weather_precip_mm?: number | null; weather_visibility?: number | null;
  };
  recent_alerts?: Array<{
    id: number;
    rule_id: string;
    priority: string;
    message: string;
    timestamp: string;
    event_type?: string;
    concern_level?: string;
    resolved_at?: string;
  }>;
  hr_enabled: boolean;
  hr_connection: string;
  hr_last_reading: number | null;
  hr_last_reading_time: string | null;
  hr_consecutive_failures: number;
  hr_error: string | null;
  gps_enabled?: boolean;
  debug?: {
    flash_summary?: string;
    flash_triggers?: Array<{ time?: string; triggers?: string[] }>;
    trigger_timers?: Record<string, number | null>;
    behavior_mode?: string;
    rule_count?: number;
    last_flash_context?: {
      timestamp?: string;
      triggers?: string[];
      summary?: string;
      should_push?: boolean;
      push_motivation?: string;
      delivery_state?: string;
      sensor_snapshot?: { hr?: number; steps?: number; idle?: string; screen?: boolean; location?: string };
      baseline_context?: string;
      pushed?: boolean;
      push_content?: string;
      grounding_rejections?: string[];
    };
  };
}

interface HrDataPoint {
  timestamp: string;
  heart_rate: number;
}

export interface RegisteredPlace {
  id: string; name: string; lat: number; lng: number; radius_m: number;
  environment: 'indoor' | 'outdoor' | 'unknown';
}

export interface PerceptionSettings {
  step_stagnation: { at_work_min: number; outdoor_min: number; at_home_min: number };
  heart_rate: { critical_high: number; sustained_high: number; sustained_high_window_min: number; abnormal_low: number; surge_delta: number };
  screen: { input_idle_suspect_sec: number; input_idle_away_sec: number; late_night_start_hour: number; late_night_end_hour: number };
  gps: { home_lat: number | null; home_lng: number | null; work_lat: number | null; work_lng: number | null; home_radius_m: number; work_radius_m: number; places?: RegisteredPlace[] };
  weather?: { city: string; auto_refresh_location: boolean };
  hr_device?: { mac: string | null; name: string | null };
  cooldowns: { critical_sec: number; high_sec: number; medium_sec: number };
}

const defaultSettings: PerceptionSettings = {
  step_stagnation: { at_work_min: 60, outdoor_min: 30, at_home_min: 120 },
  heart_rate: { critical_high: 120, sustained_high: 110, sustained_high_window_min: 5, abnormal_low: 50, surge_delta: 40 },
  screen: { input_idle_suspect_sec: 120, input_idle_away_sec: 1800, late_night_start_hour: 0, late_night_end_hour: 6 },
  gps: { home_lat: null, home_lng: null, work_lat: null, work_lng: null, home_radius_m: 200, work_radius_m: 200 },
  weather: { city: '', auto_refresh_location: false },
  hr_device: { mac: null, name: null },
  cooldowns: { critical_sec: 7200, high_sec: 3600, medium_sec: 10800 },
};

export function useSentinelStatus() {
  const [status, setStatus] = useState<SentinelStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/sentinel/status`);
      const data = await res.json();
      setStatus(data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  return { status, loading, refetch: fetchStatus };
}

/** 面板打开期间每 30s 自动刷新哨兵状态 */
export function useSentinelAutoRefresh(show: boolean, refetch: () => void) {
  useEffect(() => {
    if (!show) return;
    const timer = setInterval(refetch, 30000);
    return () => clearInterval(timer);
  }, [show, refetch]);
}

export function useSentinelHrHistory(hours = 6) {
  const [data, setData] = useState<HrDataPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${getApiBase()}/api/sentinel/hr/history?hours=${hours}`)
      .then(r => r.json())
      .then(d => setData(d.data || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [hours]);

  return { data, loading };
}

export function usePerceptionSettings() {
  const [settings, setSettings] = useState<PerceptionSettings>(defaultSettings);
  const [loading, setLoading] = useState(true);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/settings`);
      const data = await res.json();
      const stored = data.silicon_perception || {};
      setSettings({ ...defaultSettings, ...stored, gps: { ...defaultSettings.gps, ...stored.gps } });
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSettings(); }, [fetchSettings]);

  const saveSettings = useCallback(async (updated: PerceptionSettings) => {
    try {
      const res = await fetch(`${getApiBase()}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: 'silicon_perception', value: updated }),
      });
      if (!res.ok) { console.error('Sentinel settings save failed:', res.status); return false; }
      setSettings(updated);
      return true;
    } catch(e) {
      console.error('Sentinel settings save failed:', e);
      return false;
    }
  }, []);

  return { settings, loading, saveSettings, refetch: fetchSettings };
}

export function useSentinelToggle() {
  const toggle = useCallback(async () => {
    const res = await fetch(`${getApiBase()}/api/sentinel/toggle`, { method: 'POST' });
    return res.json();
  }, []);
  const toggleHr = useCallback(async () => {
    const res = await fetch(`${getApiBase()}/api/sentinel/hr/toggle`, { method: 'POST' });
    return res.json();
  }, []);
  const reconnectHr = useCallback(async () => {
    const res = await fetch(`${getApiBase()}/api/sentinel/hr/reconnect`, { method: 'POST' });
    return res.json();
  }, []);
  const toggleGps = useCallback(async () => {
    const res = await fetch(`${getApiBase()}/api/sentinel/gps/toggle`, { method: 'POST' });
    return res.json();
  }, []);
  return { toggle, toggleHr, reconnectHr, toggleGps };
}

/** 心率设备扫描：PC bleak 广播扫描 + 手机已配对列表（scan-mobile 端点 Phase C 提供） */
export function useHrDevices() {
  const scanPc = useCallback(async () => {
    const r = await fetch(`${getApiBase()}/api/sentinel/hr/scan`, { method: 'POST' });
    return r.json();
  }, []);
  const scanMobile = useCallback(async () => {
    const r = await fetch(`${getApiBase()}/api/sentinel/hr/scan-mobile`, { method: 'POST' });
    return r.json();
  }, []);
  return { scanPc, scanMobile };
}
