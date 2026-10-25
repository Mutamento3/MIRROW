import { useEffect, useRef, useState } from 'react';
import WorkshopArtifactFrame from './WorkshopArtifactFrame';
import type { Artifact } from './types';

/** Mount live previews only near the viewport; retain the same sandbox/mood bridge. */
export default function WorkshopExhibit({ artifact, apiBase, thumbnail = false }: { artifact: Artifact; apiBase: string; thumbnail?: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const [scale, setScale] = useState(1);
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (!thumbnail || !root.current) return;
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      const fit = Math.min(width / 390, height / 960);
      setScale(fit); setLeft((width - 390 * fit) / 2);
    });
    observer.observe(root.current);
    return () => observer.disconnect();
  }, [thumbnail]);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setNear(true); observer.disconnect(); }
    }, { rootMargin: '120px' });
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const image = /\.(png|jpe?g|webp|gif)$/i.test(artifact.name);
  const html = /\.html?$/i.test(artifact.name);
  const text = /\.(txt|md)$/i.test(artifact.name);
  return <div className={`workshop-exhibit-stage${thumbnail ? ' workshop-exhibit-thumbnail' : ''}`} ref={root}>
    {near && (html || text) ? <div className="workshop-exhibit-frame" style={thumbnail ? { width:390, height:960, left, transform:`scale(${scale})`, transformOrigin:'top left' } : undefined}><WorkshopArtifactFrame artifact={artifact} apiBase={apiBase} /></div>
      : near && image ? <img src={`${apiBase}/api/workshop/artifacts/${artifact.id}/download`} alt={artifact.summary || artifact.name} loading="lazy" />
      : <div className="workshop-exhibit-placeholder"><span aria-hidden="true">◇</span><p>{html || image ? '展品正在就位' : artifact.name}</p>{!html && !image && <small>打开详情查看作品说明与文件</small>}</div>}
  </div>;
}
