/**
 * 动态后端地址计算
 *
 * Capacitor App：启动前探测家庭局域网与 Tailscale 两个候选端点。
 *   家庭局域网优先；不可达时回退 Tailscale。
 * 浏览器本地：127.0.0.1:8005
 * 浏览器远程（Tailscale）：使用当前 host + :8005
 *   — 不传 token，后端通过 Tailscale IP 认证（WireGuard 密码学保证 IP 不可伪造）
 *
 * 部署配置：Capacitor App 连接的 PC 地址/端口从 Vite 环境变量读取
 *   VITE_BACKEND_HOST / VITE_BACKEND_PORT（配 ai-chat-frontend/.env）
 *   留空回退到默认值，方便开源者改成自己的 Tailscale IP。
 */

const PC_TAILSCALE_IP = (import.meta as any).env?.VITE_BACKEND_HOST || '';
const PC_LAN_IP = (import.meta as any).env?.VITE_BACKEND_LAN_HOST || '';
const BACKEND_PORT = (import.meta as any).env?.VITE_BACKEND_PORT || '8005';
const ACTIVE_ENDPOINT_KEY = 'mirrow_active_backend';

let activeMobileApiBase = PC_TAILSCALE_IP ? `http://${PC_TAILSCALE_IP}:${BACKEND_PORT}` : '';

function isCapacitor(): boolean {
  // The web SDK also registers window.Capacitor (e.g. via MiBandPanel).
  // Its presence is not evidence of an Android/iOS native bridge.
  return (window as any).Capacitor?.isNativePlatform?.() === true;
}

/** 检测当前设备平台：pc | mobile。后端据此过滤可用工具。 */
export function getPlatform(): string {
  if (isCapacitor()) return 'mobile';
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)) return 'mobile';
  return 'pc';
}

export function getApiBase(): string {
  if (isCapacitor()) {
    return activeMobileApiBase;
  }
  const host = window.location.hostname;
  if (host === '127.0.0.1' || host === 'localhost') {
    return `http://127.0.0.1:${BACKEND_PORT}`;
  }
  return `${window.location.protocol}//${host}:${BACKEND_PORT}`;
}

export function getWsBase(): string {
  if (isCapacitor()) {
    return activeMobileApiBase.replace(/^http/, 'ws');
  }
  const host = window.location.hostname;
  if (host === '127.0.0.1' || host === 'localhost') {
    return `ws://127.0.0.1:${BACKEND_PORT}`;
  }
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${wsProtocol}://${host}:${BACKEND_PORT}`;
}

/** 手机端候选地址。只返回构建时配置的固定地址，不接受运行时任意主机。 */
export function getMobileBackendCandidates(): string[] {
  const values = [
    PC_LAN_IP ? `http://${PC_LAN_IP}:${BACKEND_PORT}` : '',
    PC_TAILSCALE_IP ? `http://${PC_TAILSCALE_IP}:${BACKEND_PORT}` : '',
  ];
  return [...new Set(values.filter(Boolean))];
}

async function endpointIsReachable(apiBase: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${apiBase}/api/system/task-status`, {
      method: 'GET',
      cache: 'no-store',
      signal: controller.signal,
      headers: { 'X-Client-Platform': 'mobile' },
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

/** 必须在导入 App 及其 API 模块前调用，避免模块级 API_BASE 捕获旧端点。 */
export async function initializeBackendEndpoint(): Promise<void> {
  if (!isCapacitor()) return;

  const candidates = getMobileBackendCandidates();
  const tailCandidate = PC_TAILSCALE_IP ? `http://${PC_TAILSCALE_IP}:${BACKEND_PORT}` : '';
  for (const candidate of candidates) {
    // 蜂窝网络唤醒 Tailscale 往往慢于家庭 LAN；给远程端点更长的首次握手时间。
    if (await endpointIsReachable(candidate, candidate === tailCandidate ? 5000 : 1800)) {
      activeMobileApiBase = candidate;
      window.localStorage.setItem(ACTIVE_ENDPOINT_KEY, candidate);
      return;
    }
  }

  // 探测同时超时时不要复用上次的家庭 LAN 地址：出门后它必然不可达。
  // 选远程端点继续有界重试；若没有远程配置才保留唯一候选。
  activeMobileApiBase = tailCandidate || candidates[0] || '';
}

/** 网络切换后重新选择地址。调用方在变化时重载 WebView，刷新已缓存 API_BASE 的模块。 */
export async function refreshMobileBackendEndpoint(): Promise<boolean> {
  if (!isCapacitor()) return false;
  const previous = activeMobileApiBase;
  await initializeBackendEndpoint();
  return activeMobileApiBase !== previous;
}
