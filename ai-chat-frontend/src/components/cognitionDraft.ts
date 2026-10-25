export type BookDomain = 'self' | 'other' | 'world';
export type CognitionDraft = {
  name: string; body: string; revision: string; subject_id: string; knower_id: string;
  kind: string; core: boolean; keywords: string[]; aliases?: string[];
  owner_id?: string; scope?: string; basis?: string; state?: string; expires_at?: string;
  [key: string]: unknown;
};

// Defaults are structural, not an interpretation of the text. Existing metadata
// is loaded unchanged; no model call or guessed identity is involved.
export function newCognitionDraft(book: BookDomain, subject = 'human', interest = ''): CognitionDraft {
  return {
    name: '', body: '', revision: 'new', aliases: [], keywords: interest ? ['兴趣爱好', interest] : [], core: false,
    subject_id: book === 'self' ? 'k' : book === 'other' ? subject : 'unknown',
    knower_id: ['self', 'other'].includes(book) ? 'k' : 'unknown',
    kind: book === 'self' ? 'self_reflection' : book === 'other' ? 'interpretation' : 'fact',
    ...(book === 'other' ? { owner_id: 'k', scope: 'person', basis: 'inferred', state: 'active', expires_at: '' } : {}),
  };
}
