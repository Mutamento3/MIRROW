package com.mirrow.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import com.mirrow.app.relay.RelayWatchdogController;

/**
 * 开机/升级自启接收器（2026-08-22 新增）。
 *
 * 手机重启 / App 升级 / 时间改变后自动拉起 MirrowRelayService。
 * 补此前保活缺口：重启后 MIRROW 后台不自动拉起 → 人类伙伴要手动开 App 才恢复失联。
 * 同时延迟 8s 自愈无障碍（复用 ensureEnabled，冷启动绑定异步，立即查必为 false 会误触发 toggle）。
 *
 * ⚠️ 依赖用户在 ColorOS「自启动管理」里允许 MIRROW 自启动（见 SettingsPermissionTab 引导）。
 */
public class BootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = (intent == null || intent.getAction() == null)
            ? "" : intent.getAction();
        MainApplication.log("BOOT", "onReceive: " + action, null);
        RelayWatchdogController.onBoot(context);
        try {
            // AlarmManager 的一次性闹钟在重启/时区变化后需要重新挂回系统。
            CheckupAlarmController.restoreScheduledAlarms(context);
        } catch (Throwable t) {
            MainApplication.log("CHECKUP", "restore alarms failed: " + t.getMessage(), t);
        }
        try {
            RelayWatchdogController.triggerRepair(context, "boot");
        } catch (Throwable t) {
            MainApplication.log("BOOT", "start service failed: " + t.getMessage(), t);
        }
        try {
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                try {
                    String r = MirrowAccessibilityService.ensureEnabled(context);
                    MainApplication.log("A11Y", "boot ensureEnabled: " + r, null);
                } catch (Throwable t) {
                    MainApplication.log("A11Y", "boot ensureEnabled crash: " + t.getMessage(), t);
                }
            }, 8000);
        } catch (Throwable ignored) {}
    }
}
