import type { Artifact } from './types';
import WorkshopArtifactFrame from './WorkshopArtifactFrame';

export default function WorkshopPreview({ artifact, apiBase, onClose }: {
  artifact: Artifact; apiBase: string; onClose: () => void;
}) {
  return <><h3>{artifact.name} · 作品预览</h3><WorkshopArtifactFrame artifact={artifact} apiBase={apiBase} /><button onClick={onClose}>收起预览</button></>;
}
