import { useEffect, useState } from 'react';
import { getApiBase } from '../config';
import type { RegisteredPlace } from '../hooks/useSentinel';

type Draft = Omit<RegisteredPlace, 'lat' | 'lng' | 'radius_m'> & { lat: string; lng: string; radius_m: string };
interface Props {
  places: RegisteredPlace[];
  onSave: (places: RegisteredPlace[]) => Promise<boolean>;
  showToast: (message: string) => void;
}
const environmentNames = { indoor: '室内落脚处', outdoor: '户外地点', unknown: '室内外未知' };
const inputStyle = { width: '100%', boxSizing: 'border-box' as const, padding: '5px 7px', borderRadius: 4, border: '1px solid #888', background: 'transparent', color: 'inherit' };
const buttonStyle = { padding: '4px 9px', borderRadius: 4, border: '1px solid #888', background: 'transparent', color: 'inherit', cursor: 'pointer' };

export default function GpsPlaceSettings({ places, onSave, showToast }: Props) {
  const [saved, setSaved] = useState(places);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setSaved(places); }, [places]);

  const edit = (place?: RegisteredPlace) => {
    setError('');
    setDraft(place ? { ...place, lat: String(place.lat), lng: String(place.lng), radius_m: String(place.radius_m) }
      : { id: `place_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`, name: '', lat: '', lng: '', radius_m: '200', environment: 'indoor' });
  };
  const commit = async (next: RegisteredPlace[]) => {
    setBusy(true); setError('');
    try {
      if (!await onSave(next)) throw new Error('保存失败，请重试');
      setSaved(next); setDraft(null); showToast('落脚处已保存');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败');
    } finally { setBusy(false); }
  };
  const saveDraft = () => {
    if (!draft) return;
    const name = draft.name.trim(), lat = Number(draft.lat), lng = Number(draft.lng), radius = Number(draft.radius_m);
    if (!name || name.length > 40 || /[\u0000-\u001f]/.test(name)) { setError('请填写单行地点名称（最多40字）'); return; }
    if (!draft.lat.trim() || !draft.lng.trim() || !Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) { setError('请填写有效的纬度和经度'); return; }
    if (!Number.isFinite(radius) || radius < 20 || radius > 2000) { setError('识别半径为20–2000米'); return; }
    const place: RegisteredPlace = { id: draft.id, name, lat, lng, radius_m: radius, environment: draft.environment };
    const next = saved.some(item => item.id === place.id) ? saved.map(item => item.id === place.id ? place : item) : [...saved, place];
    void commit(next);
  };
  const useCurrentLocation = async () => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${getApiBase()}/api/sentinel/gps/test`, { method: 'POST' });
      const result = await response.json();
      if (!response.ok || !result.success || !Number.isFinite(result.lat) || !Number.isFinite(result.lng)) throw new Error('当前定位不可用，请确认手机连接与定位权限');
      setDraft(old => old ? { ...old, lat: String(result.lat), lng: String(result.lng) } : old);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '定位失败'); }
    finally { setBusy(false); }
  };

  return <section aria-label="固定落脚处" style={{ padding: '10px 12px', borderRadius: 8, marginBottom: 10, border: '1px solid rgba(0,0,0,0.12)' }}>
    <div style={{ fontWeight: 600, marginBottom: 6 }}>📍 固定落脚处</div>
    <p style={{ fontSize: '0.78em', color: '#888', margin: '4px 0 10px' }}>登记家、常去地点等地点，帮助 AI 分清离家与户外。默认室内落脚处；定位识别的是地点范围。</p>
    {saved.length === 0 && <p style={{ fontSize: '0.82em' }}>尚未登记；其他外出地点的室内外保持未知。</p>}
    {saved.map(place => <div key={place.id} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, padding: '6px 0' }}>
      <span style={{ flex: 1, minWidth: 140 }}>{place.name}<small style={{ display: 'block', color: '#888' }}>{environmentNames[place.environment]} · {place.radius_m}米</small></span>
      <button type="button" style={buttonStyle} disabled={busy} onClick={() => edit(place)} aria-label={`编辑${place.name}`}>编辑</button>
      <button type="button" style={buttonStyle} disabled={busy} onClick={() => void commit(saved.filter(item => item.id !== place.id))} aria-label={`移除${place.name}`}>移除</button>
    </div>)}
    {!draft ? <button type="button" style={buttonStyle} disabled={busy} onClick={() => edit()}>＋ 添加落脚处</button>
      : <fieldset disabled={busy} style={{ margin: '8px 0 0', padding: 10, border: '1px solid #888', borderRadius: 6 }}>
        <legend>{saved.some(place => place.id === draft.id) ? '编辑落脚处' : '新增落脚处'}</legend>
        <label style={{ display: 'block', marginBottom: 8 }}>名称<input style={inputStyle} value={draft.name} maxLength={40} placeholder="例如：家、常去地点" onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
        <label style={{ display: 'block', marginBottom: 8 }}>地点环境<select style={inputStyle} value={draft.environment} onChange={event => setDraft({ ...draft, environment: event.target.value as RegisteredPlace['environment'] })}><option value="indoor">室内落脚处</option><option value="outdoor">户外地点</option><option value="unknown">室内外未知／混合场所</option></select></label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
          <label>纬度<input type="number" step="any" style={inputStyle} value={draft.lat} onChange={event => setDraft({ ...draft, lat: event.target.value })} /></label>
          <label>经度<input type="number" step="any" style={inputStyle} value={draft.lng} onChange={event => setDraft({ ...draft, lng: event.target.value })} /></label>
        </div>
        <label style={{ display: 'block', marginTop: 8 }}>识别半径（米）<input type="number" min={20} max={2000} style={inputStyle} value={draft.radius_m} onChange={event => setDraft({ ...draft, radius_m: event.target.value })} /></label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}><button type="button" style={buttonStyle} onClick={() => void useCurrentLocation()}>使用手机当前位置</button><button type="button" style={buttonStyle} onClick={saveDraft}>保存落脚处</button><button type="button" style={buttonStyle} onClick={() => { setDraft(null); setError(''); }}>取消</button></div>
      </fieldset>}
    {error && <p role="alert" style={{ color: '#f59e0b', fontSize: '0.82em' }}>{error}</p>}
    {busy && <p role="status" style={{ fontSize: '0.82em' }}>处理中…</p>}
  </section>;
}
