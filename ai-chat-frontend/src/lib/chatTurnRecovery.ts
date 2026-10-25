export type ChatTurnStatus = 'running' | 'completed' | 'failed' | 'unknown';
export type ChatTurnRecovery = ReturnType<typeof createChatTurnRecovery>;

/** Recover an accepted turn's lifecycle, never infer it from a reply bubble. */
export function createChatTurnRecovery(options: {
  readStatus: () => Promise<ChatTurnStatus>;
  onTerminal: (status: 'completed' | 'failed') => void;
  isVisible: () => boolean;
  schedule?: (callback: () => void) => ReturnType<typeof setTimeout>;
  cancel?: (timer: ReturnType<typeof setTimeout>) => void;
}) {
  let stopped = false;
  let checking = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let release!: () => void;
  const finished = new Promise<void>(resolve => { release = resolve; });
  const schedule = options.schedule ?? (callback => setTimeout(callback, 3000));
  const cancel = options.cancel ?? clearTimeout;

  const dispose = () => {
    stopped = true;
    if (timer !== undefined) cancel(timer);
    timer = undefined;
    release();
  };
  const check = async (receivedStatus?: 'failed') => {
    // A matching WS failure receipt is already authoritative, even if the
    // status endpoint or a foreground transition is delayed.
    if (stopped) return;
    if (receivedStatus) {
      dispose();
      options.onTerminal(receivedStatus);
      return;
    }
    if (checking || !options.isVisible()) return;
    if (timer !== undefined) cancel(timer);
    timer = undefined;
    checking = true;
    try {
      const status = await options.readStatus();
      if (stopped) return;
      if (status === 'completed' || status === 'failed') {
        // The authoritative terminal receipt ends generation. UI refresh is
        // owned by the caller and must not hold this lock or replay the model.
        dispose();
        options.onTerminal(status);
      }
    } catch {
      // Network failures and unknown turns cannot certify completion.
    } finally {
      checking = false;
      if (!stopped && options.isVisible()) {
        timer = schedule(() => { timer = undefined; void check(); });
      }
    }
  };
  return { check, dispose, finished, get stopped() { return stopped; } };
}
