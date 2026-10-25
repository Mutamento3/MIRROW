/** Compact wall timestamps, using the viewer's local calendar (not elapsed hours). */
export function formatSocialTimestamp(timestamp: number, now = new Date()): string {
  const date = new Date(timestamp * 1000);
  if (!Number.isFinite(timestamp) || Number.isNaN(date.getTime())) return '—';
  const pad = (value: number) => String(value).padStart(2, '0');
  const sameYear = date.getFullYear() === now.getFullYear();
  if (sameYear && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()) {
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  const day = `${pad(date.getMonth() + 1)}/${pad(date.getDate())}`;
  return sameYear ? day : `${date.getFullYear()}/${day}`;
}
