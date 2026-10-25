export interface StatusMeta {
  legacy: string;
  location: string;
  activity_category: string;
  rest_mode: string;
  source: string;
}

export interface PlaceDisplay {
  name: string;
  environment: string;
  expiresAt: number;
  wifiConnected: boolean | null;
  wifiExpiresAt: number;
}

// Retain only display facts, not addresses, coordinates or network identifiers.
type Freshness = { state?: string; age_seconds?: number; max_age_seconds?: number };
interface SentinelDisplayInput {
  running?: boolean;
  last_snapshot?: {
    location_place_name?: string | null;
    location_environment?: string | null;
    location_label?: string;
  } | null;
  source_freshness?: { gps_location?: Freshness; wifi_state?: Freshness };
}
interface WifiDisplayInput { connected?: boolean; is_home?: boolean; connection_state?: string }

function expiresAt(value: Freshness | undefined, now: number): number {
  if (value?.state !== 'fresh' || !Number.isFinite(value.age_seconds)
      || !Number.isFinite(value.max_age_seconds)) return 0;
  return now + Math.max(0, value.max_age_seconds! - value.age_seconds!) * 1000;
}

export function projectPlaceDisplay(status: SentinelDisplayInput, wifi: WifiDisplayInput | null, now = Date.now()): PlaceDisplay | null {
  const snapshot = status.last_snapshot;
  const name = snapshot?.location_place_name;
  const environment = snapshot?.location_environment || 'unknown';
  const setting = environment === 'indoor' ? '室内落脚处' : environment === 'outdoor' ? '户外地点' : '室内外未知';
  const gpsExpiry = expiresAt(status.source_freshness?.gps_location, now);
  // The backend label already admits only a fresh GPS-place match and gives home WiFi priority.
  if (!status.running || !name || gpsExpiry <= now
      || snapshot?.location_label !== `外出·${name}（${setting}）`) return null;
  const wifiExpiry = expiresAt(status.source_freshness?.wifi_state, now);
  const wifiFresh = wifi?.connection_state === 'connected' && wifiExpiry > now;
  if (wifiFresh && wifi?.is_home === true) return null;
  return {
    name, environment, expiresAt: gpsExpiry,
    wifiConnected: wifiFresh && typeof wifi?.connected === 'boolean' ? wifi.connected : null,
    wifiExpiresAt: wifiExpiry,
  };
}

export function placePresentation(place: PlaceDisplay | null, now = Date.now()): { label: string; detail: string } | null {
  if (!place || place.expiresAt <= now) return null;
  const wifi = place.wifiExpiresAt > now ? place.wifiConnected : null;
  const indoor = place.environment === 'indoor' && wifi === true;
  const setting = place.environment === 'indoor' ? '室内落脚处' : place.environment === 'outdoor' ? '户外地点' : '室内外未知';
  const fact = `当前位于${place.name}范围内${wifi === true ? '且连接WiFi' : wifi === false ? '且未连接WiFi' : ''}`;
  return {
    label: `外出·${place.name}（${indoor ? '室内' : setting}）`,
    detail: indoor ? `${fact}，推测在${place.name}室内` : `${fact}，登记环境：${setting}`,
  };
}

export function toHierarchicalLabel(legacy: string, meta?: StatusMeta | null, placeLabel?: string): string {
  const oldMap: Record<string, string> = {
    gaming: '🎮 游戏', out: '🚪 外出', bathing: '🛁 洗澡', eating: '🍽 吃饭',
    napping: '😴 小憩', sleeping: '🌙 睡眠', idle: '💤 空闲', other: '📝 其他', coding: '💻 写代码',
  };
  if (!meta) return legacy === 'out' && placeLabel ? `🚪 ${placeLabel}` : oldMap[legacy] || legacy;
  const loc = meta.location === 'out' ? (placeLabel || '外出') : meta.location === 'home' ? '在家' : '';
  const actMap: Record<string, string> = {
    gaming: '🎮 游戏', coding: '💻 代码', eating: '🍽 吃饭', bathing: '🛁 洗澡',
    sleeping: '🌙 睡眠', napping: '😴 小憩', idle: '💤 空闲', other: '📝 其他',
  };
  const act = actMap[meta.activity_category] || meta.activity_category;
  if (!loc) return act;
  if (meta.activity_category === 'idle' && meta.location === 'out') return `🚪 ${loc}`;
  return `${loc}·${act}`;
}
