import { useCallback, useEffect, useRef } from 'react';
import type { Artifact } from './types';

export default function WorkshopArtifactFrame({ artifact, apiBase }: { artifact: Artifact; apiBase: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const currentArtifact = useRef(artifact.id);
  const requestId = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  currentArtifact.current = artifact.id;
  const sendMood = useCallback(async () => {
    if (document.hidden) return;
    const request = ++requestId.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    let mood = { status: 'unavailable', family: null, checked_at: new Date().toISOString() };
    try {
      const response = await fetch(`${apiBase}/api/workshop/runtime/${artifact.id}/mood`, { cache: 'no-store', signal: controller.signal });
      if (response.ok) mood = await response.json();
    } catch { /* Clear a formerly available value when the connection is lost. */ }
    finally { window.clearTimeout(timeout); }
    if (request === requestId.current && currentArtifact.current === artifact.id) frame.current?.contentWindow?.postMessage({ type: 'mirrow-workshop-mood', version: 1, artifact_id: artifact.id, mood }, '*');
  }, [apiBase, artifact.id]);
  useEffect(() => {
    const timer = window.setInterval(() => { void sendMood(); }, 15000);
    const visible = () => { if (!document.hidden) void sendMood(); };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); requestId.current += 1; activeRequest.current?.abort(); };
  }, [sendMood]);
  return <iframe ref={frame} title={artifact.name} sandbox="allow-scripts allow-forms" src={`${apiBase}/api/workshop/artifacts/${artifact.id}/preview`} onLoad={() => void sendMood()} />;
}
