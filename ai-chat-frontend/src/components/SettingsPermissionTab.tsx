import React, { useEffect } from 'react';
import { getPlatform } from '../config';

/**
 * 权限设置页 — 无障碍权限（仅移动端 APK 可见）。
 * 走 Capacitor 原生插件，不经过后端 HTTP。
 * mount 时自动查询一次真实状态（只读查询，不弹窗、不骚扰用户）。
 */
interface Props {
  a11yStatus: string;
  setA11yStatus: (s: string) => void;
}

export const SettingsPermissionTab: React.FC<Props> = ({ a11yStatus, setA11yStatus }) => {
  const [showGrantHint, setShowGrantHint] = React.useState(false);
  // mount 时自动检测真实状态（只读查询 + 静默自愈，无弹窗）
  useEffect(() => {
    if (getPlatform() !== 'mobile') return;
    const capWin = window as any;
    if (!capWin.Capacitor?.Plugins?.MobileTool) {
      setA11yStatus('插件未加载（请用 APK）');
      return;
    }
    capWin.Capacitor.Plugins.MobileTool.execute({ tool: 'accessibility_status', params: '{}' })
      .then((result: any) => {
        if (result?.content) setA11yStatus(result.content);
        setShowGrantHint(result?.write_secure_granted === false);
      }).catch(() => {});
  }, []);
  if (getPlatform() !== 'mobile') {
    return (
      <div className="settings-section">
        <h4>权限设置</h4>
        <p className="help-text">权限设置仅在手机 App（APK）中可用。当前为桌面/浏览器端。</p>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <h4>📱 权限设置</h4>

      {/* 无障碍权限 */}
      <div style={{ marginBottom: 16 }}>
        <p style={{ fontWeight: 600 }}>无障碍权限（屏幕感知）: {a11yStatus}</p>
        <p className="help-text">
          开启无障碍权限后，AI 可以通过截图工具查看手机界面，感知当前前台应用。屏幕感知需要该服务保持开启。
        </p>
        <button
          onClick={async () => {
            try {
              const capWin = window as any;
              if (!capWin.Capacitor?.Plugins?.MobileTool) { setA11yStatus('插件未加载（请用 APK）'); return; }
              const r = await capWin.Capacitor.Plugins.MobileTool.execute({ tool: 'accessibility_status', params: '{}' });
              setA11yStatus(r?.content || JSON.stringify(r));
              setShowGrantHint(r?.write_secure_granted === false);
              // 运行中或已自动修复 → 不跳系统设置；只有自愈也无能为力时才跳
              if (r?.running === false && !String(r?.self_heal || '').startsWith('healed')) {
                await capWin.Capacitor.Plugins.MobileTool.execute({ tool: 'open_accessibility_settings', params: '{}' });
                setA11yStatus((r?.content || '') + '\n已打开无障碍设置 → 找到「MIRROW 屏幕感知」开启');
              }
            } catch (e: any) {
              setA11yStatus('检测失败: ' + (e?.message || '未知错误'));
            }
          }}
          style={{ fontSize: '0.8em', padding: '2px 10px', borderRadius: 4, border: '1px solid #888', cursor: 'pointer' }}
        >🖼️ 检测/开启无障碍截图</button>
        {showGrantHint && (
          <div style={{ marginTop: 4, color: 'var(--text-secondary)', fontSize: '0.82em' }}>
            <p>自愈能力需一次性 ADB 授权（重启不丢，仅卸载重装后需重跑）：</p>
            <code style={{ fontSize: '0.75em', userSelect: 'all', wordBreak: 'break-all' }}>adb shell pm grant com.mirrow.app android.permission.WRITE_SECURE_SETTINGS</code>
            <p style={{ marginTop: 2 }}>ColorOS 拦截 pm grant 时改用安装时授权：</p>
            <code style={{ fontSize: '0.75em', userSelect: 'all', wordBreak: 'break-all' }}>adb install -r -g app-debug.apk</code>
          </div>
        )}
      </div>

      {/* 后台保活（ColorOS 白名单引导，2026-08-22） */}
      <div style={{ borderTop: '1px solid rgba(0,0,0,0.1)', paddingTop: 12, marginTop: 12 }}>
        <p style={{ fontWeight: 600 }}>🔋 后台保活（OPPO/ColorOS）</p>
        <p className="help-text">
          后台被杀是 OPPO 系（ColorOS）默认省电策略。MIRROW 已内置前台服务 + 开机自启 + 自动重连，
          但需在系统设置里加白才能长久存活：
        </p>
        <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--text-secondary)', fontSize: '0.85em' }}>
          <li>「设置 → 应用 → MIRROW → 耗电管理」→ 开启「允许后台运行」</li>
          <li>「设置 → 应用 → MIRROW → 自启动」→ 允许（重启手机后自动拉起）</li>
          <li>「设置 → 电池 → 电池优化」→ 选择 MIRROW → 不优化</li>
          <li>「设置 → 应用 → 特殊权限 → 闹钟和提醒」→ 允许精确闹钟（看门狗的系统兜底）</li>
          <li>多任务界面长按 MIRROW 卡片 → 加锁（防一键清理杀掉）</li>
        </ol>
      </div>

    </div>
  );
};

export default SettingsPermissionTab;
