type Row = { id: string; content: string };

export function captureDisplayRows<T extends Row>(rows: T[], pendingIds: ReadonlySet<string> = new Set()) {
  // Copy membership: the mutation may finish before this GET returns.
  return new Map(rows.map(row => [row.id, { row, content: row.content, pending: pendingIds.has(row.id) }]));
}

/** Accept missing persisted replies, but keep edits/streams newer than the GET. */
export function protectNewerDisplayRows<T extends Row>(current: T[], incoming: T[], snapshot: ReturnType<typeof captureDisplayRows<T>>, pendingIds: ReadonlySet<string> = new Set()) {
  const latest = new Map(current.map(row => [row.id, row]));
  return incoming.map(row => {
    const now = latest.get(row.id);
    const before = snapshot.get(row.id);
    return now && (pendingIds.has(row.id) || before?.pending || !before || now !== before.row || now.content !== before.content) ? now : row;
  });
}
