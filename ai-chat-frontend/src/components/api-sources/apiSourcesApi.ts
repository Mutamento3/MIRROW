import { getApiBase } from '../../config';

export type ModelSource = { id: string; label: string; model: string; profile: string; json_mode: string; thinking: string; system_role: string; sampling: boolean; enabled: boolean; roles?: string[]; allowed_nodes?: string[] };
export type Station = { id: string; name: string; base_url: string; protocol: string; key_env: string; key_configured?: boolean; enabled: boolean; models: ModelSource[] };
export type TaskGroup = { id: string; label: string; description: string; prefix_family?: string; prefer_official_flash?: boolean; nodes: { id: string; label: string; description?: string; function_family?: string; function_label?: string; default_category?: string; single_source?: boolean }[] };
export type CategoryPolicy = { peak_enabled: boolean; constant: string[]; peak: string[]; valley: string[] | null; schedule: 'official' | 'custom'; windows: {start: string; end: string}[]; weekdays: number[]; holiday_dates: string[]; retry_rounds?: number; retry_delay_seconds?: number; expiry_seconds?: number };
export type TaskCategory = {id: string; label: string; groups: TaskGroup[]};
export type ApiConfig = { stations: Station[]; chains: Record<string, string[]>; overrides: Record<string, string[]>; timeout_seconds: number; revision: string; groups: TaskGroup[]; profiles: Record<string, string>; policies?: Record<string, CategoryPolicy>; assignments?: Record<string,string>; categories?: TaskCategory[]; stable_sources?: Record<string,string> };
export type Attempt = { id: string; task_id: string; category: string; node: string; station_id: string; station_name: string; source_id: string; model: string; ordinal: number; started_at: string; ended_at: string | null; duration_ms: number | null; status: string; error_code: string; http_status: number; trace_id: string; input_tokens: number | null; output_tokens: number | null };
export type CallSummary = { category: string; node: string; station_id: string; station_name: string; source_id: string; model: string; calls: number; success: number; failures: number; refusals: number; running: number; input_tokens: number | null; output_tokens: number | null; cached_input_tokens?: number | null; cache_reported_input_tokens?: number | null };
export type CallsSnapshot = { summary: CallSummary[]; tasks: { id: string; status: string; attempts: Attempt[] }[]; total_tasks: number; page: number; page_size: number; updated_at: string; nodes: string[]; node_labels?: Record<string, string>; category_labels?: Record<string, string>; stations?: { id: string; name: string }[] };

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${getApiBase()}/api/api-sources/${path}`, { ...options, cache: 'no-store', signal: options.signal ?? AbortSignal.timeout(20000), headers: { 'Content-Type': 'application/json', ...options.headers } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '配置字段有误，请检查后重试');
  return body as T;
}

export const sourceOptions = (config: ApiConfig) => config.stations.flatMap(station => station.models.map(model => ({ id: model.id, label: `${station.name} · ${model.label}`, hint: model.model, allowedNodes: model.allowed_nodes, disabled: !station.enabled || !model.enabled || !station.key_configured, station })));
export const newId = (prefix: string) => `${prefix}_${crypto.randomUUID().replaceAll('-', '').slice(0, 16)}`;

export const profileDefaults = (profile: string): Pick<ModelSource, 'json_mode' | 'thinking' | 'system_role' | 'sampling'> => ({
  json_mode: profile === 'generic' ? 'prompt' : 'json_object',
  thinking: ['siliconflow-qwen', 'siliconflow-deepseek', 'qwen', 'deepseek', 'glm'].includes(profile) ? 'off' : 'default',
  system_role: profile === 'gpt-reasoning' ? 'developer' : 'system',
  sampling: profile !== 'gpt-reasoning',
});
