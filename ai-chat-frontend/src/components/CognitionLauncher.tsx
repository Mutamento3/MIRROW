import { useState } from 'react';
import CognitionPanel from './CognitionPanel';

export default function CognitionLauncher({ domain, onChanged }: { domain: 'self' | 'world'; onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  return <><button className="btn-secondary" onClick={() => setOpen(true)}>认知整理</button>
    {open && <CognitionPanel initialDomain={domain} onClose={() => { setOpen(false); onChanged?.(); }} />}</>;
}
