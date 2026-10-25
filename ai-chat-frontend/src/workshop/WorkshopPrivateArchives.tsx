import type { Catalog } from './cabinet';

const time = (value: string) => value.replace('T', ' ').slice(0, 16);

export default function WorkshopPrivateArchives({ archive, offset, onPage }: {
  archive: Catalog['private_archives']; offset: number; onPage: (offset: number) => void;
}) {
  if (!archive?.total) return null;
  return <section className="workshop-private-archives" aria-label="保密工单记录">
    <header><h3>保密工单记录</h3><small>{archive.total} 单已归档</small></header>
    <p>这里保留状态和时间，内容由 AI 决定何时公开。</p>
    <ul>{archive.jobs.map(job => <li key={job.id}>
      <div><strong>🔒 AI 的保密工单</strong><small>#{job.id.slice(0, 8)}</small></div>
      <span className="workshop-private-archive-state">AI 已验收 · 已归档</span>
      <small>创建于 {time(job.created_at)}</small>
      <small>{job.accepted_at ? `验收于 ${time(job.accepted_at)}` : `记录更新于 ${time(job.updated_at)}`}</small>
      <small>尚未公开</small>
    </li>)}</ul>
    {archive.total > 50 && <div className="workshop-pagination">
      <button disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - 50))}>上一页记录</button>
      <span>第 {Math.floor(offset / 50) + 1} 页</span>
      <button disabled={!archive.has_more} onClick={() => onPage(offset + 50)}>下一页记录</button>
    </div>}
  </section>;
}
