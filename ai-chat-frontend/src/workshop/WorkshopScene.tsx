






export type WorkshopTab = 'orders' | 'works' | 'settings';
export type SceneIconName = WorkshopTab | 'back' | 'close' | 'refresh' | 'invite';

export function SceneIcon({ name }: { name: SceneIconName }) {
  return <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'orders' && <><path d="M4 7c5-2 8-1 12 2 4-3 7-4 12-2v18c-5-2-8-1-12 2-4-3-7-4-12-2Z" /><path d="M16 9v18M8 12l4 1M8 17l4 1M20 13l4-1M20 18l4-1" /></>}
    {name === 'works' && <><path d="M8 23V13l8-6 8 6v10ZM12 23v-7h8v7M5 26h22M6 26V13a10 10 0 0 1 20 0v13" /><path d="m14 4 2-2 2 2" /></>}
    {name === 'settings' && <><path d="m13 3 6 0 1 4 3 2 4-1 3 5-3 3v3l3 3-3 5-4-1-3 2-1 3h-6l-1-3-3-2-4 1-3-5 3-3v-3l-3-3 3-5 4 1 3-2Z" /><circle cx="16" cy="17" r="5" /></>}
    {name === 'back' && <path d="m18 7-9 9 9 9M9 16h18" />}
    {name === 'close' && <path d="m8 8 16 16M24 8 8 24" />}
    {name === 'refresh' && <><path d="M26 13a10 10 0 0 0-18-4L4 13M4 6v7h7M6 19a10 10 0 0 0 18 4l4-4M21 19h7v7" /></>}
    {name === 'invite' && <><path d="M27 15a11 11 0 0 1-11 11H5l3-5a11 11 0 1 1 19-6Z" /><path d="M11 15h10M16 10v10" /></>}
  </svg>;
}

const entries: { tab: WorkshopTab; label: string; shortLabel: string }[] = [
  { tab: 'orders', label: '工单与房间', shortLabel: '工单' },
  { tab: 'works', label: '作品柜', shortLabel: '作品' },
  { tab: 'settings', label: '工作坊设置', shortLabel: '设置' },
];

export default function WorkshopScene({ model, label, reviewerLabel, remaining, checkedAt, floor, total, onOpen, onBack, onInvite, inviting, collaborating=false, inviteDisabled, onRefresh, refreshing }: {
  model: string; label: string; remaining: number | null; checkedAt: number | null; floor: number; total: number;
  reviewerLabel: string;
  paused?: boolean;
  onOpen: (tab: WorkshopTab) => void; onBack: () => void;
  collaborating?:boolean; onInvite: () => void; inviting: boolean; inviteDisabled: boolean;
  onRefresh: () => void; refreshing: boolean;
}) {
  return <>
    <div className="workshop-world" aria-label="阳光下的小机手工作坊">
      <div className="workshop-stage">
        <div className="workshop-background" style={{background:"linear-gradient(135deg,#eef3ed,#dedbd2)",width:"100%",height:"100%"}} aria-label="工作坊通用背景" />
        <div className={`workshop-figurine ${model === 'gpt-6-astra' ? 'is-astra' : 'is-sol'}`}>
          <span className="workshop-figurine-glow" />
          <span className="workshop-contact-shadow" />
          <span aria-label="工具助手" style={{fontSize:"3rem"}}>◈</span>
          <span className="workshop-warm-reflection" />
        </div>
        <div className="workshop-sign" aria-label={`当前工具手 ${label}`}>
          <span>{label}</span>
        </div>
        <div className="workshop-figurine workshop-reviewer">
          <span className="workshop-figurine-glow" /><span className="workshop-contact-shadow" />
          <span aria-label="工具助手" style={{fontSize:"3rem"}}>◈</span>
          <span className="workshop-warm-reflection" />
        </div>
        <div className="workshop-reviewer-sign" aria-label={`独立质检 ${reviewerLabel}`}><span>{reviewerLabel}</span></div>
      </div>
    </div>
    <header className="workshop-scene-header">
      <button className="workshop-quiet-button" onClick={onBack} aria-label="返回机生活" title="返回机生活"><SceneIcon name="back" /></button>
      <div><small>AI'S LITTLE WORKSHOP</small><h1>小机手工作坊</h1></div>
    </header>
    <nav className="workshop-scene-nav" aria-label="工作坊场景入口">
      <button className="workshop-scene-entry" aria-label="邀请 AI 来工作坊" title="邀请 AI 来工作坊" disabled={inviting || inviteDisabled} onClick={onInvite}><SceneIcon name="invite" /><span>{collaborating ? '协作中' : inviting ? '来访中' : '邀请'}</span></button>
      {entries.map(entry => <button key={entry.tab} className="workshop-scene-entry" aria-label={entry.label} aria-haspopup="dialog" title={entry.label} onClick={() => onOpen(entry.tab)}>
        <SceneIcon name={entry.tab} /><span>{entry.shortLabel}</span>
        {entry.tab === 'orders' && total > 0 && <span className="workshop-entry-count" aria-label={`${total} 份工单`}>{total}</span>}
      </button>)}
    </nav>
    <footer className="workshop-scene-footer"><p>一个小想法，一件带回家的作品。</p><span>账号剩余 {remaining === null ? '需刷新确认' : `${remaining}%`} <i /> 开工下限 {floor}% <button className="workshop-quota-refresh" onClick={onRefresh} disabled={refreshing} aria-label="刷新账号额度"><SceneIcon name="refresh" /></button></span>{checkedAt && <small>最近查询 {new Date(checkedAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · 后台定期更新</small>}</footer>
  </>;
}
