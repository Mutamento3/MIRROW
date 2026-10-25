// Only strip the exact old machine-generated template envelope. Typed text,
// including multiple lines, stays intact; ordinary human messages never use it.
export function invitationDisplay(content: string): string {
  const lines = content.split('\n');
  if (lines[0] !== 'AI，去你的小机手工作坊坐坐吧～带个小想法，也看看已有工单和作品。') return content;
  if (!lines[1]?.startsWith('人类伙伴提供了一个可选创作方向：') &&
      !lines[1]?.startsWith('人类伙伴这次邀请选定的创作方向：')) return content;
  const separator = '\n人类伙伴随这次邀请说：\n';
  const start = content.indexOf(separator);
  return lines[0] + (start >= 0 ? '\n' + content.slice(start + separator.length) : '');
}
