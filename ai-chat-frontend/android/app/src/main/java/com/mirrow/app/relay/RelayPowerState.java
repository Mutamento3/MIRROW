package com.mirrow.app.relay;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;
import android.os.PowerManager;

/** Reads only current Android power facts used to select a watchdog lease length. */
public final class RelayPowerState {
    private RelayPowerState() {}

    public static long watchdogLeaseMs(Context context) {
        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        boolean powerSave = power != null && power.isPowerSaveMode();
        boolean idle = power != null && power.isDeviceIdleMode();
        return (powerSave || idle) ? 10 * 60_000L : 15 * 60_000L;
    }

    public static String label(Context context) {
        PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
        boolean powerSave = power != null && power.isPowerSaveMode();
        boolean idle = power != null && power.isDeviceIdleMode();
        Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int status = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
        boolean charging = status == BatteryManager.BATTERY_STATUS_CHARGING
            || status == BatteryManager.BATTERY_STATUS_FULL;
        if (idle) return "device_idle";
        if (powerSave) return "power_save";
        return charging ? "charging" : "normal";
    }
}
