package com.mirrow.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** AlarmManager 到点入口；不依赖 WebSocket 或前台 Service。 */
public class CheckupAlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String eventId = intent == null ? "" : intent.getStringExtra(CheckupAlarmController.EXTRA_EVENT_ID);
        if (eventId != null && !eventId.isEmpty()) {
            CheckupAlarmController.onAlarmFired(context, eventId);
            // Alarm broadcasts are one of Android's allowed background
            // activity launch cases. Open the durable alarm surface directly;
            // the high-priority notification remains the fallback.
            try {
                org.json.JSONObject record = CheckupAlarmController.getRecord(context, eventId);
                Intent alarm = new Intent(context, CheckupAlarmActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        .putExtra(CheckupAlarmController.EXTRA_EVENT_ID, eventId)
                        .putExtra(CheckupAlarmController.EXTRA_TITLE,
                                record == null ? "MIRROW查岗" : record.optString(CheckupAlarmController.EXTRA_TITLE, "MIRROW查岗"))
                        .putExtra(CheckupAlarmController.EXTRA_BODY,
                                record == null ? "查岗提醒" : record.optString(CheckupAlarmController.EXTRA_BODY, "查岗提醒"));
                context.startActivity(alarm);
            } catch (Exception ignored) {}
        }
    }
}
