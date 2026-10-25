import {useCallback, useEffect, useState} from 'react';
import {getApiBase} from '../config';
import {createSyncedMode} from '../lib/syncedMode';
import {requestDndMode} from '../lib/dndMode';

export default function DndSettingToggle() {
  const [value, setValue] = useState<boolean | null>(null);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [control] = useState(() => createSyncedMode({
    initial: false,
    request: (method, next) => requestDndMode(`${getApiBase()}/api/settings`, method, next),
    publish: setValue, publishPending: setPending,
    onError: () => setError('勿扰切换未获保存确认，请核对当前状态。'),
  }));
  const read = useCallback(async () => {
    setLoading(true);
    setError('');
    const token = control.captureRead();
    try {
      const actual = await requestDndMode(`${getApiBase()}/api/settings`, 'GET');
      control.acceptSnapshot(actual, token);
    } catch {
      setError('勿扰状态读取未获确认。');
    } finally {
      setLoading(false);
    }
  }, [control]);
  useEffect(() => {void read();}, [read]);
  return (
    <>
      <label className="toggle">
        <input type="checkbox" checked={value === true}
          disabled={loading || pending || value === null}
          onChange={() => {setError(''); void control.toggle();}} />
        <span className="toggle-slider"></span>
        {value === null ? '勿扰状态未确认' : '开启勿扰模式'}
      </label>
      {loading && <p className="help-text" role="status">正在读取勿扰状态…</p>}
      {error && <p className="help-text" role="alert">{error}</p>}
      {error && <button type="button" disabled={loading || pending} onClick={() => void read()}>重新读取</button>}
    </>
  );
}
