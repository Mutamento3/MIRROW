import { memo, useId } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkFullwidthStrong, rehypeFootnoteLabels, remarkUserLiteralTildes } from '../lib/chatMarkdown';
import './ChatMarkdown.css';

const plugins = [remarkGfm, remarkFullwidthStrong];
const userPlugins = [remarkGfm, remarkUserLiteralTildes, remarkFullwidthStrong];
const components: Components = {
  a: ({ href, children, title, id, className, 'aria-label': ariaLabel, 'aria-describedby': describedBy }) => href
    ? <a href={href} title={title} id={id} className={className} aria-label={ariaLabel} aria-describedby={describedBy}
      target={href.startsWith('#') ? undefined : '_blank'} rel={href.startsWith('#') ? undefined : 'noopener noreferrer'}
      onClick={event => event.stopPropagation()}>{children}</a>
    : <span>{children}</span>,
  // Uploaded/generated images already have controlled attachment renderers.
  // Markdown image syntax stays useful as a link, without a second media UI.
  img: ({ src, alt }) => src
    ? <a className="chat-markdown-image-link" href={src} target="_blank" rel="noopener noreferrer" onClick={event => event.stopPropagation()}>{alt || '图片链接'}</a>
    : <span>{alt || '图片链接'}</span>,
  table: ({ children }) => <div className="chat-markdown-scroll" role="region" aria-label="表格，可横向滚动" tabIndex={0}>
    <table>{children}</table>
  </div>,
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
};

export default memo(function ChatMarkdown({ content, isUser = false }: { content: string; isUser?: boolean }) {
  const id = useId();
  const prefix = `chat-${id.replace(/[^a-zA-Z0-9_-]/g, '')}-`;
  if (!content.trim()) return null;
  return <div className="chat-markdown">
    <Markdown remarkPlugins={isUser ? userPlugins : plugins} rehypePlugins={[[rehypeFootnoteLabels, prefix]]} components={components}
      remarkRehypeOptions={{ clobberPrefix: prefix, footnoteLabel: '注释', footnoteBackLabel: '返回正文' }}>
      {content}
    </Markdown>
  </div>;
});
