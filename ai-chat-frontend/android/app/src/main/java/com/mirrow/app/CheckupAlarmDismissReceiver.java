package com.mirrow.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** 通知栏“按掉”动作入口。 */
public class CheckupAlarmDismissReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String eventId = intent == null ? "" : intent.getStringExtra(CheckupAlarmController.EXTRA_EVENT_ID);
        if (eventId != null && !eventId.isEmpty()) {
            CheckupAlarmController.dismiss(context, eventId);
        }
    }
}
