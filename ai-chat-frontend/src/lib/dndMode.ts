/** Exact settings receipt for the shared switch reconciliation controller. */
export async function requestDndMode(
  url: string, method: 'GET' | 'POST', value?: boolean, request: typeof fetch = fetch,
): Promise<boolean> {
  const response = await request(url, method === 'POST' ? {
    method, headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: 'dnd_enabled', value}),
  } : {method});
  if (!response.ok) throw new Error('DND HTTP request failed');
  const data = await response.json();
  if (!data || typeof data.dnd_enabled !== 'boolean'
      || (method === 'POST' && data.status !== 'ok')) {
    throw new Error('DND response unconfirmed');
  }
  return data.dnd_enabled;
}
