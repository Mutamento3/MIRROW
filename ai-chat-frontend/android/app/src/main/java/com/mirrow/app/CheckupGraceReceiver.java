package com.mirrow.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** 闹钟被按掉后的五分钟宽限期入口。 */
public class CheckupGraceReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String eventId = intent == null ? "" : intent.getStringExtra(CheckupAlarmController.EXTRA_EVENT_ID);
        if (eventId != null && !eventId.isEmpty()) {
            CheckupAlarmController.onGraceExpired(context, eventId);
        }
    }
}
