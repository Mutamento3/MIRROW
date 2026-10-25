import { parsePersistedImageMessage } from './imageMessage';
import { markdownBubbleSegments } from './chatMarkdown';

export function splitMessageBubbles(content: string, role: 'user' | 'assistant' | 'parallel_summary'): string[] {
  if (role === 'user') {
    content = parsePersistedImageMessage(content).visibleContent;
  } else if (role === 'assistant') {
    // Real voice attachments own their display; these are storage envelopes.
    content = content.replace(/\[语音消息\]（[\s\S]*?）(?=\n|$)/g, '')
      .replace(/\[语音消息\]/g, '');
  }
  return markdownBubbleSegments(content, role === 'user');
}
