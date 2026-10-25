export type PersistedImageMessage = {
  description: string | null;
  visibleContent: string;
};

// The description can itself contain Chinese closing parentheses.  Only a
// closing parenthesis immediately followed by a line break/end terminates the
// storage envelope.
const IMAGE_DESCRIPTION_BLOCK = /^\[图片消息\]（([\s\S]*?)）(?=\r?\n|$)\r?\n?/;
const PLAIN_IMAGE_MARKER = /^\[图片消息\](?:\r?\n)?/;

export function parsePersistedImageMessage(content: string): PersistedImageMessage {
  if (!content) return { description: null, visibleContent: '' };

  const matched = content.match(IMAGE_DESCRIPTION_BLOCK);
  if (matched) {
    return {
      description: matched[1].trim() || null,
      visibleContent: content.slice(matched[0].length).trim(),
    };
  }

  return {
    description: null,
    visibleContent: content.replace(PLAIN_IMAGE_MARKER, '').trim(),
  };
}
