import { getApiBase } from '../config';
import DndSettingToggle from './DndSettingToggle';

interface Props {
  userStatusMode: string;
  setUserStatusMode: (mode: string) => void;
}

export default function UserStatusSettings({ userStatusMode, setUserStatusMode }: Props) {
  return (
    <>
      <div className="settings-section">
        <h4>状态模式</h4>
        <label className="toggle">
          <input
            type="checkbox"
            checked={userStatusMode === 'manual'}
            onChange={e => {
              const mode = e.target.checked ? 'manual' : 'auto';
              setUserStatusMode(mode);
              fetch(`${getApiBase()}/api/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'user_status_mode', value: mode }),
              }).catch(() => {});
            }}
          />
          <span className="toggle-slider"></span>
          手动模式（完整9状态，MIRROW原生体验）
        </label>
        <p className="help-text">
          自动模式（默认）：只暴露游戏/外出/睡眠/未知——100%铁证自动切换，简洁省心。<br/>
          手动模式：全部9个状态细分（含写代码/洗澡/吃饭/小憩），自动切换照样跑，分类更细。
        </p>
      </div>

      <div className="settings-section">
        <h4>睡眠切出</h4>
        <label style={{ display: 'block', marginTop: '0.5rem' }}>
          <span>睡够多少小时后发消息/玩手机才切出: </span>
          <input
            type="number"
            min="0.5" max="12" step="0.5"
            defaultValue={4}
            style={{ width: '5rem', marginLeft: '0.5rem' }}
            onChange={e => {
              const v = parseFloat(e.target.value);
              if (v > 0) {
                fetch(`${getApiBase()}/api/settings`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ key: 'sleep_wake_threshold_hours', value: v }),
                }).catch(() => {});
              }
            }}
          />
          <span style={{ marginLeft: '0.3rem', color: '#888' }}>小时 (默认4)</span>
        </label>
        <p className="help-text">
          睡眠期间，前端启动即切出；发消息或手机连续活动15分钟，需睡够此时间后才切出。
        </p>
      </div>

      <div className="settings-section">
        <h4>手动勿扰</h4>
        <DndSettingToggle />
        <p className="help-text">
          一刀压制 AI 的所有主动出声：哨兵提醒、漫想推送、状态切换表达全部暂停。
        </p>
      </div>
    </>
  );
}
