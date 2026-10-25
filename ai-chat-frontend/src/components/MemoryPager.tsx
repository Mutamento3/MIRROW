import {useEffect, useState} from 'react';

export default function MemoryPager({offset, total, pageSize, loading, label, onChange}: {
  offset: number; total: number; pageSize: number; loading: boolean; label: string;
  onChange: (offset: number) => void;
}) {
  const page=Math.floor(offset/pageSize)+1;
  const pages=Math.max(1,Math.ceil(total/pageSize));
  const [draft,setDraft]=useState(String(page));
  useEffect(()=>setDraft(String(page)),[page]);
  return <nav className="memory-library-pages" aria-label={`${label}分页`}>
    <button type="button" disabled={loading||offset===0} onClick={()=>onChange(Math.max(0,offset-pageSize))}>上一页</button>
    <form className="memory-page-jump" onSubmit={event=>{
      event.preventDefault();
      const requested=Number(draft);
      if(loading||!Number.isInteger(requested)||requested<1)return;
      const next=Math.min(pages,requested);
      setDraft(String(next));onChange((next-1)*pageSize);
    }}>
      <label><span className="memory-page-label">第</span><input type="number" inputMode="numeric" aria-label={`${label}页码`} min={1} max={pages} disabled={loading} value={draft} onChange={event=>setDraft(event.target.value)}/><span>/ {pages} 页</span></label>
      <button type="submit" disabled={loading} aria-label={`跳转${label}页码`}>前往</button>
    </form>
    <button type="button" disabled={loading||offset+pageSize>=total} onClick={()=>onChange(offset+pageSize)}>下一页</button>
  </nav>;
}
