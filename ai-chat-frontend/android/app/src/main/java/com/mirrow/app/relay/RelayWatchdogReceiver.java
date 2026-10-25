package com.mirrow.app.relay;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.PowerManager;

/** Alarm receiver deliberately does no I/O: it only starts the foreground relay repair path. */
public class RelayWatchdogReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (RelayWatchdogController.ACTION_RELAY_WATCHDOG.equals(action)) {
            RelayWatchdogController.onWatchdogAlarm(context);
        } else if (PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED.equals(action)
                || PowerManager.ACTION_POWER_SAVE_MODE_CHANGED.equals(action)) {
            RelayWatchdogController.onPowerStateChanged(context);
        }
    }
}
