import { useEffect, useState } from 'react';
import { cognitionRequest } from './cognitionApi';
import './CognitionProfile.css';

type Source = { n: number; domain: 'self' | 'other' | 'world'; name: string; revision: string; state: 'current' | 'changed' | 'missing' | 'merged'; redirect?: {domain: Source['domain']; name: string; entry_id: string; revision: string} | null };
type Point = { text: string; display_text?: string; references: number[]; injection_available?: boolean; temporal?: { period: 'longstanding' | 'recent' | 'unspecified'; as_of?: string; review_after?: string } };
type Profile = {
  status: 'current' | 'previous' | 'partial' | 'stale' | 'refreshing' | 'missing' | 'unavailable';
  subjects: Record<string, string>; generated_at?: string; source_date?: string; version?: string;
  injection_available: boolean; source: string; sources: Source[];
  scopes?: Record<string, string>;
  scope_audit?: { subject_id: string; scope: string; source_valid_candidates: number; approved_points: number }[];
  identities: { subject_id: string; name: string; aliases: string[] }[];
  capsules: { subject_id: string; scope: string; points: Point[] }[];
};
const statusText: Record<Profile['status'], string> = {
  current: '当前画像', previous: '上一版画像 · 新版尚未完成', stale: '来源已改变 · 以下是旧画像，不代表当前认识',
  partial: '部分认识待更新 · 来源未变的认识继续使用',
  refreshing: '刷新尚未完成 · 以下是已保存画像', missing: '画像尚未建立', unavailable: '画像读取未确认',
};
const periods = [['longstanding', '长期认识'], ['recent', '近期认识'], ['unspecified', '时效未明确']] as const;
const bookNames = { self: '自我书', other: '他者书', world: '世界书' };
const scopeNames: Record<string, string> = { general: '通识', writing: '写作', life_rhythm: '生活节律', music: '音乐', gaming: '游戏', social: '交往' };
function dateLabel(value?: string) {
  if (!value) return '未记录';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '日期未确认' : parsed.toLocaleString('zh-CN');
}

export default function CognitionProfile({ initialSubject = 'human', onOpenSource }: {
  initialSubject?: string; onOpenSource: (source: Source) => void;
}) {
  const [subject, setSubject] = useState(initialSubject);
  const [scope, setScope] = useState('general');
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    cognitionRequest('/brief/inspection').then(data => {
      if (active) setProfile(data);
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [generation]);
  const capsule = profile?.capsules.find(item => item.subject_id === subject && item.scope === scope);
  const identity = profile?.identities.find(item => item.subject_id === subject);
  const audit = profile?.scope_audit?.find(item => item.subject_id === subject && item.scope === scope);
  return <section className="cognition-profile" aria-label="画像阅读">
    <header className="profile-header"><div><h2>画像</h2><p>对你们的简短认识，细节仍在认知书里。</p></div>
      <button disabled={loading} onClick={() => setGeneration(value => value + 1)}>刷新查看</button></header>
    {loading && <p role="status">正在读取已保存画像…</p>}
    {error && <p role="alert">{error}{profile ? '；以下保留上次读取，不代表已取得最新画像。' : ''}</p>}
    {profile && <>
      <p className={`profile-status profile-status-${profile.status}`} role="status">{(error || loading) && '上次读取：'}{statusText[profile.status]}</p>
      <p className="profile-explanation">{profile.source}。{error || loading ? '最新来源版本尚未核对。' : profile.status === 'partial' ? '标记为待更新的认识暂不注入，其余认识仍可读取。' : profile.injection_available ? '来源版本有效，可供相应上下文选择读取。' : '此处仅用于查看已保存结果，当前来源有效性尚未满足。'}这里不是某一轮的实际注入记录；查看和刷新按钮均不调用模型。</p>
      {profile.version && <p className="profile-meta">生成于 {dateLabel(profile.generated_at)}{profile.source_date && ` · 整理活跃日 ${profile.source_date}`}<br />{profile.version}</p>}
      <div className="profile-selectors">
        <nav aria-label="画像对象">{['human', 'k'].map(id => <button key={id} aria-pressed={subject === id} onClick={() => setSubject(id)}>{profile.subjects[id] || id}</button>)}</nav>
        <nav aria-label="画像领域">{Object.entries(profile.scopes || scopeNames).map(([id, label]) => <button key={id} aria-pressed={scope === id} onClick={() => setScope(id)}>{label}</button>)}</nav>
      </div>
      {identity && <p className="profile-identity">{identity.name}{identity.aliases.length > 0 && ` · ${identity.aliases.join('、')}`}</p>}
      {audit && <p className="profile-meta">本版选出 {audit.source_valid_candidates} 条来源有效候选，复核保留 {audit.approved_points} 条。未选出不等于原书中没有相关认识。</p>}
      {!['missing', 'unavailable'].includes(profile.status) && (capsule?.points.length ? periods.map(([period, label]) => {
        const points = capsule.points.filter(point => (point.temporal?.period || 'unspecified') === period);
        return points.length > 0 && <section className="profile-period" key={period} aria-label={label}><h3>{label}</h3>
          {points.map((point, index) => <article className="profile-point" key={index}><p>{point.display_text ?? point.text}</p>
            {point.injection_available === false && <small>来源已修订 · 本条等待更新，暂不注入</small>}
            {period === 'recent' && <small>观察截至 {point.temporal?.as_of || '未记录'}{point.temporal?.review_after && ` · 复核日 ${point.temporal.review_after}（复核不等于已发生变化）`}</small>}
            <details><summary>来源书页</summary><ul>{point.references.map(n => {
              const source = profile.sources.find(item => item.n === n);
              return <li key={n}>{source ? <><button disabled={source.state === 'missing'} onClick={() => onOpenSource(source.redirect ? {...source, ...source.redirect} : source)}>{bookNames[source.domain]} · {source.name}</button>
                <small>{source.state === 'current' ? '与画像来源版本一致' : source.state === 'changed' ? '已修订，打开的是当前书页' : source.state === 'merged' ? '已归并，打开的是现行书页，不是旧来源版本' : '当前书页已不可用'}</small></> : <span>来源绑定未确认</span>}</li>;
            })}</ul></details></article>)}
        </section>;
      }) : <p className="profile-empty">这一对象的{(profile.scopes || scopeNames)[scope] || scope}画像暂未记录可展示的认识。</p>)}
    </>}
  </section>;
}
