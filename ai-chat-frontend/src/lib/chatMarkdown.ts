import type { PhrasingContent, Root, RootContent, Text } from 'mdast';
import type { Root as HtmlRoot, RootContent as HtmlContent } from 'hast';
import type { Plugin } from 'unified';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { codeFenced } from 'micromark-core-commonmark';
import type { Extension } from 'micromark-util-types';

// User tone marks stay literal, including ~~ and a line of ~~~~. Disable
// tilde syntax at parsing time while retaining standard backtick code fences.
export const remarkUserLiteralTildes: Plugin<[], Root> = function () {
  const data = this.data();
  const extensions = data.micromarkExtensions || (data.micromarkExtensions = []);
  const extension: Extension = {
    disable: { null: ['strikethrough', 'codeFenced'] },
    flow: { 96: { ...codeFenced, name: 'chatBacktickCodeFenced' } },
  };
  extensions.push(extension);
};

// Segmentation and display share the same Markdown grammar. Parse source
// positions rather than cutting list/table/code contents with line regexes.
const parser = unified().use(remarkParse).use(remarkGfm);
const userParser = unified().use(remarkParse).use(remarkGfm).use(remarkUserLiteralTildes);

export function markdownBubbleSegments(content: string, isUser = false): string[] {
  if (!content.trim()) return [];
  const tree = (isUser ? userParser : parser).parse(content);
  // Definitions belong to the entire document, including forward references.
  if (tree.children.some(node => node.type === 'definition' || node.type === 'footnoteDefinition')) {
    return [content.replace(/^\r?\n+|(?:\r?\n)+$/g, '')];
  }
  return tree.children.flatMap((node: RootContent) => {
    const source = content.slice(node.position?.start.offset, node.position?.end.offset);
    if (!source.trim()) return [];
    // Preserve the one-line chat rhythm even with inline bold on one line.
    // A syntax node spanning lines must remain together to keep its grammar.
    if (node.type === 'paragraph' && !node.children.some(child => child.type !== 'text'
      && child.position?.start.line !== child.position?.end.line)) {
      return source.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    }
    return [source];
  });
}

export const remarkFullwidthStrong: Plugin<[], Root> = () => tree => {
  function walk(parent: { children: unknown[] }) {
    parent.children = parent.children.flatMap(child => {
      const node = child as { type: string; children?: unknown[]; value?: string };
      if (node.type === 'text' && typeof node.value === 'string') {
        const parts: PhrasingContent[] = [];
        let offset = 0;
        for (const match of node.value.matchAll(/＊＊([^＊\r\n]+)＊＊/g)) {
          if (match.index > offset) parts.push({ type: 'text', value: node.value.slice(offset, match.index) });
          parts.push({ type: 'strong', children: [{ type: 'text', value: match[1] }] });
          offset = match.index + match[0].length;
        }
        if (!parts.length) return [child];
        if (offset < node.value.length) parts.push({ type: 'text', value: node.value.slice(offset) } as Text);
        return parts;
      }
      if (node.children) walk(node as { children: unknown[] });
      return [child];
    });
  }
  walk(tree);
};

export const rehypeFootnoteLabels: Plugin<[string], HtmlRoot> = prefix => tree => {
  // mdast prefixes footnote targets, but its accessible heading ID is fixed.
  // Scope that heading and its references to this mounted message as well.
  function walk(node: HtmlRoot | HtmlContent) {
    if (node.type === 'element') {
      if (node.properties.id === 'footnote-label') node.properties.id = `${prefix}footnote-label`;
      const describedBy = node.properties.ariaDescribedBy;
      if (Array.isArray(describedBy)) {
        node.properties.ariaDescribedBy = describedBy.map(value => value === 'footnote-label' ? `${prefix}footnote-label` : value);
      } else if (describedBy === 'footnote-label') {
        node.properties.ariaDescribedBy = [`${prefix}footnote-label`];
      }
    }
    if ('children' in node) node.children.forEach(walk);
  }
  walk(tree);
};
