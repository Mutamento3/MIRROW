import { useEffect, useState } from 'react';
import CognitionReader from '../components/CognitionReader';
import CognitionMaintenance from '../components/CognitionMaintenance';
import CognitionProfile from '../components/CognitionProfile';
import EntityNamesEditor from '../components/EntityNamesEditor';
import VoiceIdentityEnrollment from '../components/VoiceIdentityEnrollment';
import CognitionInterests from '../components/CognitionInterests';
import BodyKnowledge from '../components/BodyKnowledge';

import {useCognitionUnread} from '../hooks/useCognitionUnread';
import '../components/CognitionPanel.css';
import './CognitionReading.css';

export type CognitionBook = 'self' | 'other' | 'world';
const books: Record<CognitionBook, string> = { self: '自我书', other: '他者书', world: '世界书' };

export default function CognitionPage({ initialBook = 'self', initialSection = 'content', onBack, onMachineLife }: {
  initialBook?: CognitionBook; initialSection?: string; onBack: () => void; onMachineLife?: () => void;
}) {
  const [book, setBook] = useState<CognitionBook>(initialBook);
  const unread = useCognitionUnread();
  const [section, setSection] = useState(['legacy','feedback','tools','suggestions'].includes(initialSection) ? 'updates' : initialSection);
  const [dirty, setDirty] = useState(false);
  const [sourceEntry, setSourceEntry] = useState('');
  
  
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  const navigate = (action: () => void) => {
    if (dirty && !window.confirm('离开会放弃尚未保存的输入，继续？')) return;
    setDirty(false); action();
  };
  return <main className="cognition-page reading-page" aria-label="认知书">
    <div className="cognition-panel">
      <header className="reading-header"><button className="btn-secondary" onClick={() => navigate(onBack)}>← 返回</button><div><h2>认知书</h2></div></header>
      <nav aria-label="认知书籍">{Object.entries(books).map(([id, label]) =>
        <button key={id} aria-pressed={book === id} onClick={() => navigate(() => { setBook(id as CognitionBook); setSourceEntry(''); setSection('content'); })}>{label}{!!unread[id]&&<span className="cognition-unread-dot" aria-label={`${unread[id]} 条未读更新`}>●</span>}</button>)}</nav>
      <div className="reading-toolbar">
        {section !== 'content' ? <button onClick={() => navigate(() => setSection('content'))}>← 返回书页</button> : <span>{books[book]}</span>}
        <div className="reading-utilities">
          <button aria-pressed={section === 'profile'} onClick={() => navigate(() => setSection('profile'))}>画像</button>
          {book === 'other' && <button onClick={() => navigate(() => setSection('names'))}>人物称呼</button>}
          {book === 'other' && <button onClick={() => navigate(() => setSection('voice'))}>声纹录入</button>}
          {book === 'self' && <button onClick={() => navigate(() => setSection('interests'))}>兴趣爱好</button>}
          {book === 'self' && <button aria-pressed={section === 'body'} onClick={() => navigate(() => setSection('body'))}>我的身体</button>}
        </div>
      </div>
      <div className="reading-content" key={`${book}-${section}`}>
        {section === 'body' && book === 'self' && <BodyKnowledge />}
        {section === 'profile' && <CognitionProfile initialSubject={book === 'self' ? 'k' : 'human'} onOpenSource={source => navigate(() => {
          setBook(source.domain); setSourceEntry(source.name); setSection('content');
        })} />}
        {section === 'names' && book === 'other' && <EntityNamesEditor onDirtyChange={setDirty} />}
        {section === 'voice' && book === 'other' && <VoiceIdentityEnrollment />}
        {section === 'content' && <CognitionReader book={book} initialEntry={sourceEntry} onDirtyChange={setDirty} />}
        {section === 'interests' && book === 'self' && <>
          {onMachineLife && <div className="reading-toolbar"><span>兴趣认识来自 AI 的真实体验；存档和播放记录仍在机生活。</span><button onClick={() => navigate(onMachineLife)}>前往机生活</button></div>}
          <CognitionInterests onDirtyChange={setDirty} navigate={navigate} />
        </>}
        {(section === 'pending' || section === 'updates') && <CognitionMaintenance domainFilter={book} initialFilter={section === 'pending' ? (book === 'world' ? 'observing' : 'pending') : 'all'} />}
      </div>
      {section !== 'updates' && <footer className="book-footer"><button onClick={() => navigate(() => setSection('updates'))}>{book === 'world' ? '世界书演变记录' : '全书更新记录'}</button></footer>}
    </div>
  </main>;
}
