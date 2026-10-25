/** 剥离 OB_Rev wikilink 双链标记，仅保留链接文本 */
export function stripWikilinks(s: string): string {
  return (s || '').replace(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g, '$1');
}

/** Cron 表达式 → 中文可读描述 */
export function cronToChinese(cron: string): string {
  if (!cron) return '-';
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return cron;

  const [min, hour, dom, mon, dow] = parts;

  // 每N分钟: */N * * * *
  if (min.startsWith('*/') && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    const n = min.slice(2);
    return `每${n}分钟`;
  }

  // 每N小时: M */N * * * 或 0 */N * * *
  if (hour.startsWith('*/') && dom === '*' && mon === '*' && dow === '*') {
    const n = hour.slice(2);
    const m = min === '*' ? '0' : min.padStart(2, '0');
    return `每${n}小时（整点${m}分）`;
  }

  // 每天: M H * * *
  if (dom === '*' && mon === '*' && dow === '*') {
    const h = hour.padStart(2, '0');
    const m = min.padStart(2, '0');
    return `每天 ${h}:${m}`;
  }

  // 工作日: M H * * 1-5
  if (dom === '*' && mon === '*' && dow === '1-5') {
    const h = hour.padStart(2, '0');
    const m = min.padStart(2, '0');
    return `工作日 ${h}:${m}`;
  }

  // 周末: M H * * 0,6 或 6,0
  if (dom === '*' && mon === '*' && (dow === '0,6' || dow === '6,0')) {
    const h = hour.padStart(2, '0');
    const m = min.padStart(2, '0');
    return `周末 ${h}:${m}`;
  }

  // 每周某天: M H * * N
  const dowMap: Record<string, string> = { '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日' };
  if (dom === '*' && mon === '*' && dowMap[dow]) {
    const h = hour.padStart(2, '0');
    const m = min.padStart(2, '0');
    return `每${dowMap[dow]} ${h}:${m}`;
  }

  return cron;
}
