import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import CognitionReader from './CognitionReader';
import MusicExperiences from './MusicExperiences';
import { ALL_INTERESTS, UNCLASSIFIED, interestIndex, type InterestTopic } from './cognitionInterestIndex';

export default function CognitionInterests({ onDirtyChange, navigate }: {
  onDirtyChange: (value: boolean) => void; navigate: (action: () => void) => void;
}) {
  const [topics, setTopics] = useState<InterestTopic[] | null>(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(ALL_INTERESTS);
  const [experience, setExperience] = useState(false);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    let active = true;
    cognitionRequest('/topics?subject_id=k').then(data => {
      if (!Array.isArray(data.topics)) throw new Error('兴趣分类读取未确认');
      if (active) setTopics(data.topics);
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [generation]);
  if (error) return <p role="alert">{error}，请返回书页后重试。</p>;
  if (!topics) return <p role="status">正在读取兴趣分类…</p>;
  const index = interestIndex(topics);
  const current = index.find(item => item.id === selected) || index[0];
  return <>
    <div className="interest-strip" aria-label="兴趣类别">{index.map(item =>
      <button key={item.id} aria-pressed={current.id === item.id}
        onClick={() => navigate(() => { setSelected(item.id); setExperience(false); })}>{item.label}</button>)}</div>
    <p>{current.id === UNCLASSIFIED
      ? '这些书页尚有未分类内容，不代表已经确认为兴趣。打开时保留完整书页。'
      : '按书页已有的兴趣分类显示；同一兴趣可以属于多个领域。打开时保留完整书页。'}</p>
    {current.id === 'music' && <div className="reading-toolbar"><span>{experience ? '音乐体验' : '音乐兴趣认识'}</span>
      <button onClick={() => navigate(() => setExperience(!experience))}>{experience ? '查看兴趣认识' : '查看体验记录'}</button></div>}
    {experience && current.id === 'music' ? <MusicExperiences onDirtyChange={onDirtyChange} /> :
      <CognitionReader key={current.id} book="self" entryIds={current.entryIds}
        interest={[ALL_INTERESTS, UNCLASSIFIED].includes(current.id) ? '' : current.label} onDirtyChange={onDirtyChange}
        onEntriesChanged={() => setGeneration(value => value + 1)} />}
  </>;
}
