import type { Room } from './types';
import './WorkshopConversation.css';

type Message = Room['messages'][number];
const names: Record<string, string> = { k: 'AI', codex: '工作坊 Codex', human: '人类伙伴', desktop_codex: '桌面 Codex' };

export default function WorkshopConversation({ messages }: { messages: Message[] }) {
  const groups: { process: boolean; messages: Message[] }[] = [];
  for (const message of messages) {
    const process = message.role === 'codex' && message.phase !== 'final_answer';
    const last = groups.at(-1);
    if (process && last?.process) last.messages.push(message);
    else groups.push({ process, messages: [message] });
  }
  const bubble = (message: Message) => <article key={message.seq} data-speaker={message.role}>
    <small>{names[message.role] || message.role}<time>{message.created_at.replace('T', ' ').slice(5, 16)}</time></small>
    <pre>{message.body}</pre>
  </article>;
  return <div className="workshop-conversation">{groups.map(group => group.process
    ? <details className="workshop-conversation-process" key={group.messages[0].seq}>
      <summary><span>执行过程 · {group.messages.length} 条</span><small>展开查看</small></summary>
      {group.messages.some(message => message.phase !== 'commentary') && <p className="workshop-conversation-note">包含阶段未标注的早期记录，原文保留在这里。</p>}
      {group.messages.map(bubble)}
    </details> : group.messages.map(bubble))}</div>;
}
