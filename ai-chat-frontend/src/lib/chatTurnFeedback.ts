export type ChatTurnPhase = 'idle' | 'replying' | 'saving' | 'recovering' | 'syncing';

/** Display refreshes belong to one turn and never own generation admission. */
export function createChatTurnFeedback(publish: (phase: ChatTurnPhase) => void) {
  let revision = 0;
  return {
    begin() {
      const ownRevision = ++revision;
      let complete = false;
      let refreshes = 0;
      const isCurrent = () => revision === ownRevision;
      publish('replying');
      return {
        isCurrent,
        phase(phase: 'replying' | 'saving' | 'recovering') {
          if (isCurrent() && !complete) publish(phase);
        },
        complete() {
          complete = true;
          if (isCurrent()) publish(refreshes ? 'syncing' : 'idle');
        },
        async refresh(work: (isCurrent: () => boolean) => Promise<void>, onError: () => void) {
          if (!isCurrent()) return;
          refreshes++;
          if (complete) publish('syncing');
          try { await work(isCurrent); }
          catch { if (isCurrent()) onError(); }
          finally {
            refreshes--;
            if (isCurrent() && complete && !refreshes) publish('idle');
          }
        },
      };
    },
  };
}
