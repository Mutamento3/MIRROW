import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';

type Preview = { note: string; sections: { name: string; ingredient: string; position: string }[] };
export default function CognitionRecipePreview() {
  const [recipe, setRecipe] = useState('FULL_CHAT');
  const [data, setData] = useState<Preview | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setData(null); setError('');
    cognitionRequest(`/context-preview?recipe=${recipe}`).then(result => { if (active) setData(result); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [recipe]);
  return <section className="monitor-card" aria-label="配方结构预览">
    <h3>配方结构 · 声明值</h3>
    <p>这里只显示配方计划选用的原料和位置，不是实际调用记录，也不调用模型。</p>
    <label>场景 <select value={recipe} onChange={e => setRecipe(e.target.value)}>
      {['FULL_CHAT', 'GROUP_CHAT_K', 'WANDER_V2', 'WANDER_PUSH', 'SENTINEL_PUSH', 'REMINDER_PUSH', 'MUSIC_COCHLEA_PUSH', 'PHONE_BROWSE', 'RIFT_PLAY', 'SELF_TASK'].map(key => <option key={key}>{key}</option>)}
    </select></label>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">读取配方…</p>}
    {data && <><p>{data.note}</p><ol>{data.sections.map((s, i) => <li key={`${s.ingredient}-${i}`}>{s.name} · {s.ingredient} · {s.position}</li>)}</ol></>}
  </section>;
}
