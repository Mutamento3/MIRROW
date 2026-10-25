import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import SuggestionReview from './SuggestionReview';

export default function WorldBookPending() {
  const [items, setItems] = useState<{ id: string; proposed_name: string; target_entry: string; reason: string; status: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    fetch(`${getApiBase()}/api/world-book/suggestions`).then(async r => { if (!r.ok) throw new Error('读取建议失败'); return r.json(); })
      .then(data => { if (active) setItems(data.suggestions || []); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  return <section><h3>历史待审建议</h3>{error && <p role="alert">{error}</p>}{items.filter(i => i.status === 'pending').map(item => <article key={item.id}><h4>{item.proposed_name || item.target_entry}</h4><p>{item.reason}</p><button onClick={() => setSelected(item.id)}>查看建议</button></article>)}
    {selected && <SuggestionReview id={selected} onClose={() => setSelected(null)} onApplied={() => { setItems(items.filter(i => i.id !== selected)); setSelected(null); }} />}
  </section>;
}
