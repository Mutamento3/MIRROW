/** State reconciliation for the night/browse switches shared by mobile and desktop. */
type ReadToken = {generation: number; sequence: number};
type Options = {
  initial: boolean;
  request: (method: 'GET' | 'POST', value?: boolean) => Promise<boolean>;
  publish: (value: boolean) => void;
  publishPending: (pending: boolean) => void;
  onError: () => void;
};

export function createSyncedMode(options: Options) {
  let value = options.initial;
  let pending = false;
  let generation = 0;
  let sequence = 0;
  let appliedSequence = 0;
  const publish = (next: boolean) => {value = next; options.publish(next);};
  const captureRead = (): ReadToken => ({generation, sequence: ++sequence});
  const acceptSnapshot = (next: unknown, token: ReadToken) => {
    if (typeof next !== 'boolean' || pending || token.generation !== generation
        || token.sequence < appliedSequence) return false;
    appliedSequence = token.sequence;
    publish(next);
    return true;
  };
  return {
    captureRead,
    acceptSnapshot,
    async refresh() {
      const token = captureRead();
      try {acceptSnapshot(await options.request('GET'), token);} catch {
        // The last local snapshot remains available until a confirmed read arrives.
      }
    },
    async toggle() {
      if (pending) return false;
      const previous = value;
      const desired = !value;
      pending = true;
      generation++;
      options.publishPending(true);
      publish(desired);
      let confirmed = false;
      try {
        const actual = await options.request('POST', desired);
        publish(actual);
        confirmed = actual === desired;
      } catch {
        // A failed response may follow a completed write. Read once; never replay POST.
        try {
          const actual = await options.request('GET');
          publish(actual);
          // Readback confirms current runtime state, not that a rejected save succeeded.
        } catch {
          publish(previous);
        }
      } finally {
        generation++;
        pending = false;
        options.publishPending(false);
      }
      if (!confirmed) options.onError();
      return confirmed;
    },
  };
}

/** HTTP and domain failures are both failures, even when the response contains a boolean. */
export async function requestServerMode(
  url: string, field: string, method: 'GET' | 'POST', value?: boolean,
  request: typeof fetch = fetch,
): Promise<boolean> {
  const response = await request(url, method === 'POST' ? {
    method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({[field]: value}),
  } : {method});
  if (!response.ok) throw new Error('mode HTTP request failed');
  const data = await response.json();
  if (!data || data.ok === false || typeof data[field] !== 'boolean') {
    throw new Error('mode response unconfirmed');
  }
  return data[field];
}
