import { getApiBase } from '../config';

export async function cognitionRequest(path: string, body?: unknown, method = 'POST', timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${getApiBase()}/api/cognition${path}`, {
      method: body === undefined ? 'GET' : method, signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请刷新核对'), { status: response.status });
    return data;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('等待超时，结果未确认，请刷新核对后再操作');
    throw error;
  } finally { window.clearTimeout(timer); }
}

export async function cognitionPendingCounts() {
  try { return await cognitionRequest('/maintenance/counts'); }
  catch (error) {
    // An installed app and its backend may be upgraded at different times.
    if ((error as { status?: number }).status !== 404) throw error;
    const data = await cognitionRequest('/maintenance');
    const counts: Record<string, number> = {};
    for (const item of data.records) if (['pending', 'applying'].includes(item.status)) {
      const domain = item.proposal.domain;
      counts[domain] = (counts[domain] || 0) + 1;
    }
    return { counts, complete: !data.damaged_records };
  }
}
