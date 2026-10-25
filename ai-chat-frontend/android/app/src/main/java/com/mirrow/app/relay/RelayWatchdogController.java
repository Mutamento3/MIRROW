package com.mirrow.app.relay;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import com.mirrow.app.MirrowRelayService;

/**
 * Schedules one system-owned relay liveness alarm. The relay refreshes its lease
 * when the server acknowledges it; when the process is frozen, the lease simply expires.
 */
public final class RelayWatchdogController {
    public static final String ACTION_RELAY_WATCHDOG = "com.mirrow.app.action.RELAY_WATCHDOG";
    public static final String ACTION_REPAIR_RELAY = "com.mirrow.app.action.REPAIR_RELAY";
    public static final String EXTRA_REASON = "relay_repair_reason";
    private static final int REQUEST_CODE = 47231;
    private static final long RENEW_WINDOW_MS = 5 * 60_000L;

    private RelayWatchdogController() {}

    public static void onServiceStarted(Context context, String instanceId) {
        RelayWatchdogStore.serviceStarted(context, instanceId);
        renewLease(context, "service_started");
    }

    public static void onServerAcknowledged(Context context) {
        RelayWatchdogStore.serverAcknowledged(context);
        renewLease(context, "server_ack");
    }

    public static void onDisconnected(Context context, String reason) {
        RelayWatchdogStore.disconnected(context, reason);
        ensureLease(context, "disconnected");
    }

    public static void onBoot(Context context) {
        RelayWatchdogStore.triggered(context, "boot");
        schedule(context, System.currentTimeMillis() + RelayPowerState.watchdogLeaseMs(context));
    }

    public static void onPowerStateChanged(Context context) {
        RelayWatchdogStore.triggered(context, "power_state_changed");
        schedule(context, System.currentTimeMillis() + RelayPowerState.watchdogLeaseMs(context));
    }

    public static void triggerRepair(Context context, String reason) {
        RelayWatchdogStore.triggered(context, reason);
        Intent intent = new Intent(context, MirrowRelayService.class)
            .setAction(ACTION_REPAIR_RELAY)
            .setPackage(context.getPackageName())
            .putExtra(EXTRA_REASON, reason == null ? "unknown" : reason);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent);
            else context.startService(intent);
        } catch (Exception ignored) {
            // The persisted system alarm remains the recovery path if a background start is denied.
        }
    }

    public static void onWatchdogAlarm(Context context) {
        RelayWatchdogStore.triggered(context, "alarm");
        ensureLease(context, "alarm_fired");
        triggerRepair(context, "alarm");
    }

    /** Extend the lease only near expiry, avoiding an AlarmManager write per 45s heartbeat. */
    private static void renewLease(Context context, String reason) {
        long now = System.currentTimeMillis();
        long desired = now + RelayPowerState.watchdogLeaseMs(context);
        long existing = RelayWatchdogStore.nextWatchdogAt(context);
        if (existing == 0L || existing - now <= RENEW_WINDOW_MS || existing > desired) {
            schedule(context, desired);
        }
    }

    public static void ensureLease(Context context, String reason) {
        if (RelayWatchdogStore.nextWatchdogAt(context) <= System.currentTimeMillis()) {
            schedule(context, System.currentTimeMillis() + RelayPowerState.watchdogLeaseMs(context));
        }
    }

    private static void schedule(Context context, long atMillis) {
        AlarmManager alarm = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (alarm == null) return;
        PendingIntent pending = alarmPendingIntent(context);
        boolean exactAvailable = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarm.canScheduleExactAlarms();
        if (exactAvailable) {
            alarm.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pending);
        } else {
            alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pending);
        }
        RelayWatchdogStore.nextWatchdogAt(context, atMillis, exactAvailable);
    }

    private static PendingIntent alarmPendingIntent(Context context) {
        Intent intent = new Intent(context, RelayWatchdogReceiver.class)
            .setAction(ACTION_RELAY_WATCHDOG)
            .setPackage(context.getPackageName());
        return PendingIntent.getBroadcast(context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
