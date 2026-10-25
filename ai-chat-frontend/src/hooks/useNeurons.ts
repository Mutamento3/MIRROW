import { useState, useEffect, useCallback } from 'react';
import { getApiBase } from '../config';

interface NeuronTraceSummary {
  trace_id: string;
  neuron_id: string;
  timestamp: string;
  status: string;
  duration_ms: number;
  input_preview: string;
  output_preview: string;
  thinking_preview: string;
  input_full: string;
  output_full: string;
  thinking_full: string;
  error_message: string;
  recipe_used: string;
  model_used: string;
}

interface NeuronStatusItem {
  neuron_id: string;
  name: string;
  category: string;
  subsystem: string;
  consumer: string;
  trigger_desc: string;
  recipe: string;
  is_llm: boolean;
  llm_model: string;
  description: string;
  emoji: string;
  last_trace: NeuronTraceSummary | null;
  execution_count: number;
  success_count: number;
  last_ok_at: string | null;
}

interface NeuronStats {
  total: number;
  cognitive: number;
  reflex: number;
  perception: number;
  with_recipe: number;
  hard_built: number;
  migration_pct: number;
  never_executed: number;
}

interface NeuronListResponse {
  neurons: NeuronStatusItem[];
  stats: NeuronStats;
}

interface NeuronHistoryResponse {
  neuron_id: string;
  traces: NeuronTraceSummary[];
  error?: string;
}

export function useNeuronStatus() {
  const [neurons, setNeurons] = useState<NeuronStatusItem[]>([]);
  const [stats, setStats] = useState<NeuronStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchNeurons = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const base = getApiBase();
      const resp = await fetch(`${base}/neurons`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: NeuronListResponse = await resp.json();
      setNeurons(data.neurons || []);
      setStats(data.stats || null);
    } catch (e: any) {
      setError(e.message || '获取神经元状态失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNeurons();
  }, [fetchNeurons]);

  return { neurons, stats, loading, error, refresh: fetchNeurons };
}

export function useNeuronHistory(neuronId: string | null) {
  const [traces, setTraces] = useState<NeuronTraceSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!neuronId) {
      setTraces([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const base = getApiBase();
        const resp = await fetch(`${base}/neurons/${neuronId}/history?limit=20`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: NeuronHistoryResponse = await resp.json();
        if (!cancelled) setTraces(data.traces || []);
      } catch (e: any) {
        if (!cancelled) setError(e.message || '获取历史失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [neuronId]);

  return { traces, loading, error };
}
