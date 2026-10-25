import { useState } from 'react';
import { showToast } from './Toast';
import { useMainChatModel } from '../hooks/useMainChatModel';
import type { MainChatModelProfile } from '../hooks/useMainChatModel';
import MainChatSourceDialog, { mainChatSourceLabel } from './MainChatSourceDialog';

export default function MainChatModelSettings() {
  const { state, switching, switchProfile } = useMainChatModel();
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const groups = new Map<string, { label: string; profiles: MainChatModelProfile[] }>();
  state?.profiles.forEach(profile => {
    const id = profile.group_id || profile.id;
    if (!groups.has(id)) groups.set(id, { label: profile.group_label || profile.label, profiles: [] });
    groups.get(id)!.profiles.push(profile);
  });
  const active = state?.profiles.find(profile => profile.id === state.active_profile_id);
  const opened = openGroup ? groups.get(openGroup) : null;

  const confirmSource = async (profile: MainChatModelProfile) => {
    if (profile.id === state?.active_profile_id) return;
    await switchProfile(profile.id);
    showToast('success', `主聊天已切到 ${profile.group_label || profile.label} · ${mainChatSourceLabel(profile)}`);
  };

  const choose = async (profileId: string, label: string) => {
    if (profileId === state?.active_profile_id || switching) return;
    try {
      await switchProfile(profileId);
      showToast('success', `主聊天已切到 ${label}`);
    } catch (error) {
      showToast('error', error instanceof Error ? error.message : '切换主聊天模型失败');
    }
  };

  return (
    <div className="settings-section main-model-settings-section">
      <h4>主聊天大脑 · 手动切换</h4>
      {!state ? (
        <p className="help-text">正在读取后端模型配置…</p>
      ) : (
        <div className="main-model-settings-grid" role="radiogroup" aria-label="主聊天模型">
          {Array.from(groups, ([id, group]) => {
            const selected = group.profiles.some(profile => profile.id === state.active_profile_id);
            const available = group.profiles.some(profile => profile.available);
            const profile = group.profiles[0];
            return (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`main-model-settings-card${selected ? ' active' : ''}`}
                disabled={!available || switching}
                aria-haspopup={group.profiles.length > 1 ? 'dialog' : undefined}
                onClick={() => group.profiles.length > 1 ? setOpenGroup(id) : void choose(profile.id, profile.label)}
              >
                <span className="main-model-settings-title">
                  <span className="main-model-radio" aria-hidden="true">{selected ? '●' : '○'}</span>
                  {group.label}
                </span>
                <span className="main-model-settings-capability">
                  {!available
                    ? `未配置：${profile.missing_env.join('、')}`
                    : group.profiles.length > 1
                      ? selected && active ? `当前：${mainChatSourceLabel(active)} · 点击更换线路` : `${group.profiles.length} 条供应商线路 · 点击选择`
                      : profile.supports_vision
                      ? '文字与图片均由此模型处理'
                      : '纯文字；图片交给 Flash Vision'}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {active && <div className="main-model-current-source" role="status">当前主聊天：<strong>{active.group_label || active.label}</strong> · {mainChatSourceLabel(active)}</div>}
      <p className="help-text">选择其他模型后，主聊天优先使用手动选项，失败只提示，不自动切站或换模型；选回 DS Pro 时恢复下方主大脑策略。切换立即保存，从下一次聊天生效，不改变后台任务模型。</p>
      {opened && state && <MainChatSourceDialog label={opened.label} profiles={opened.profiles}
        activeId={state.active_profile_id} switching={switching} onConfirm={confirmSource} onClose={() => setOpenGroup(null)} />}
    </div>
  );
}
