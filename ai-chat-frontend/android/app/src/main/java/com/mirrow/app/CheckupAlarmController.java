package com.mirrow.app;

import android.Manifest;
import android.app.AlarmManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.os.Build;
import android.os.SystemClock;
import android.provider.Settings;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * 查岗一次性原生闹钟。
 *
 * AlarmManager 负责本地定时；WS 只负责设置 ACK 和 fired/dismissed/grace_expired
 * 状态同步。闹钟、宽限计时和待同步事件都落在手机本地，后台 relay 断开时不影响响铃。
 */
public final class CheckupAlarmController {
    private static final String TAG = "MirrowCheckupAlarm";
    private static final String PREFS = "mirrow_checkup_alarm";
    private static final String KEY_RECORDS = "records";
    private static final String KEY_EVENTS = "events";

    public static final String ACTION_ALARM_TRIGGER = "com.mirrow.app.action.CHECKUP_ALARM_TRIGGER";
    public static final String ACTION_GRACE_TRIGGER = "com.mirrow.app.action.CHECKUP_GRACE_TRIGGER";
    public static final String ACTION_DISMISS = "com.mirrow.app.action.CHECKUP_ALARM_DISMISS";
    public static final String ACTION_FLUSH = "com.mirrow.app.action.FLUSH_CHECKUP_EVENTS";

    public static final String EXTRA_EVENT_ID = "event_id";
    public static final String EXTRA_TITLE = "title";
    public static final String EXTRA_BODY = "body";
    public static final String EXTRA_TRIGGER_AT = "trigger_at_ms";

    public static final String CHANNEL_ID = "mirrow_checkup_alarm";
    private static final long GRACE_MS = 5 * 60_000L;
    private static final long RESTORE_OVERDUE_GRACE_MS = 10 * 60_000L;
    private static final Object LOCK = new Object();

    private CheckupAlarmController() {}

    public interface EventSender {
        boolean send(JSONObject message);
    }

    public static JSONObject schedule(Context context, JSONObject params) {
        String eventId = params == null ? "" : params.optString(EXTRA_EVENT_ID, "");
        long triggerAt = params == null ? 0 : params.optLong(EXTRA_TRIGGER_AT, 0L);
        String title = params == null ? "MIRROW查岗" : params.optString(EXTRA_TITLE, "MIRROW查岗");
        String body = params == null ? "查岗提醒" : params.optString(EXTRA_BODY, "查岗提醒");
        if (eventId.isEmpty()) return failure("缺少 event_id");
        if (triggerAt <= System.currentTimeMillis() + 1000L) return failure("闹钟触发时间无效");

        synchronized (LOCK) {
            JSONObject existing = findRecord(context, eventId);
            if (existing != null) {
                String state = existing.optString("state", "");
                // alarm_failed 表示系统闹钟并未挂上，允许后端重试；其余活动状态
                // 直接返回原记录，保证 relay 重试不会重复创建闹钟。
                if (!"cancelled".equals(state) && !"resolved".equals(state)
                        && !"alarm_failed".equals(state)) {
                    return success(existing, "已存在本地闹钟，返回原状态");
                }
            }

            String permissionError = checkSchedulePermissions(context);
            if (!permissionError.isEmpty()) return failure(permissionError);

            try {
                ensureAlarmChannel(context);
                int requestCode = requestCode(eventId);
                PendingIntent alarmIntent = alarmPendingIntent(context, eventId, title, body, triggerAt, requestCode);
                PendingIntent showIntent = activityPendingIntent(context, eventId, title, body, requestCode + 1);
                AlarmManager alarmManager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
                if (alarmManager == null) return failure("系统闹钟服务不可用");

                AlarmManager.AlarmClockInfo info = new AlarmManager.AlarmClockInfo(triggerAt, showIntent);
                alarmManager.setAlarmClock(info, alarmIntent);

                JSONObject record = new JSONObject();
                record.put(EXTRA_EVENT_ID, eventId);
                record.put("state", "scheduled");
                record.put(EXTRA_TITLE, title);
                record.put(EXTRA_BODY, body);
                record.put(EXTRA_TRIGGER_AT, triggerAt);
                record.put("alarm_id", eventId);
                record.put("scheduled_at", System.currentTimeMillis());
                upsertRecord(context, record);

                JSONObject result = success(record, "手机原生闹钟已设置");
                result.put("alarm_id", eventId);
                result.put("trigger_at_ms", triggerAt);
                result.put("source", "android_alarm_manager");
                return result;
            } catch (SecurityException e) {
                return failure("系统拒绝精确闹钟: " + e.getMessage());
            } catch (Exception e) {
                MainApplication.log("CHECKUP", "schedule failed: " + e.getMessage(), e);
                return failure("闹钟设置失败: " + e.getMessage());
            }
        }
    }

    public static JSONObject cancel(Context context, JSONObject params) {
        String eventId = params == null ? "" : params.optString(EXTRA_EVENT_ID, "");
        if (eventId.isEmpty()) return failure("缺少 event_id");
        synchronized (LOCK) {
            JSONObject record = findRecord(context, eventId);
            if (record == null) return success(new JSONObject(), "本地没有该闹钟");
            cancelOsAlarms(context, eventId);
            try {
                record.put("state", "cancelled");
                record.put("cancelled_at", System.currentTimeMillis());
                upsertRecord(context, record);
            } catch (Exception ignored) {}
            return success(record, "手机闹钟已取消");
        }
    }

    public static JSONObject getRecord(Context context, String eventId) {
        synchronized (LOCK) {
            return findRecord(context, eventId);
        }
    }

    public static void onAlarmFired(Context context, String eventId) {
        synchronized (LOCK) {
            JSONObject record = findRecord(context, eventId);
            if (record == null || !"scheduled".equals(record.optString("state", ""))) return;
            long now = System.currentTimeMillis();
            try {
                record.put("state", "fired");
                record.put("fired_at", now);
                upsertRecord(context, record);
            } catch (Exception ignored) {}
            queueEvent(context, record, "fired", now, 0L);
            showAlarmNotification(context, record, false);
        }
    }

    public static void dismiss(Context context, String eventId) {
        synchronized (LOCK) {
            JSONObject record = findRecord(context, eventId);
            if (record == null) return;
            String state = record.optString("state", "");
            if ("cancelled".equals(state) || "resolved".equals(state) || "dismissed".equals(state)
                    || "grace_expired".equals(state)) return;

            long now = System.currentTimeMillis();
            long graceDue = now + GRACE_MS;
            try {
                record.put("state", "dismissed");
                record.put("dismissed_at", now);
                record.put("grace_due_at", graceDue);
                upsertRecord(context, record);
            } catch (Exception ignored) {}

            cancelNotification(context, eventId);
            try {
                scheduleGraceAlarm(context, record, graceDue);
                queueEvent(context, record, "dismissed", now, graceDue);
            } catch (Exception e) {
                // 宽限计时权限在闹钟创建后被撤回时，不能让 Activity 崩溃；
                // 直接把“无法继续计时”同步给后端，由后端即时收束事件。
                MainApplication.log("CHECKUP", "schedule grace failed: " + e.getMessage(), e);
                try { record.put("state", "alarm_failed"); record.put("error", e.getMessage()); upsertRecord(context, record); }
                catch (Exception ignored) {}
                queueEvent(context, record, "alarm_failed", System.currentTimeMillis(), 0L);
            }
        }
    }

    public static void onGraceExpired(Context context, String eventId) {
        synchronized (LOCK) {
            JSONObject record = findRecord(context, eventId);
            if (record == null || !"dismissed".equals(record.optString("state", ""))) return;
            long now = System.currentTimeMillis();
            try {
                record.put("state", "grace_expired");
                record.put("grace_expired_at", now);
                upsertRecord(context, record);
            } catch (Exception ignored) {}
            queueEvent(context, record, "grace_expired", now, record.optLong("grace_due_at", 0L));
        }
    }

    /** 开机/时区变化后恢复 AlarmManager（系统闹钟不会跨重启自动保留）。 */
    public static void restoreScheduledAlarms(Context context) {
        synchronized (LOCK) {
            JSONArray records = loadArray(context, KEY_RECORDS);
            long now = System.currentTimeMillis();
            for (int i = 0; i < records.length(); i++) {
                JSONObject record = records.optJSONObject(i);
                if (record == null) continue;
                String state = record.optString("state", "");
                String eventId = record.optString(EXTRA_EVENT_ID, "");
                if (eventId.isEmpty()) continue;
                if ("scheduled".equals(state)) {
                    long triggerAt = record.optLong(EXTRA_TRIGGER_AT, 0L);
                    if (triggerAt > now + 1000L) {
                        try { scheduleOsAlarm(context, record, triggerAt); }
                        catch (Exception e) { markAlarmFailure(context, record, e.getMessage()); }
                    } else if (isWithinRestoreGrace(now, triggerAt)) {
                        onAlarmFired(context, eventId);
                    } else {
                        expireRestoredRecord(context, record, now);
                    }
                } else if ("dismissed".equals(state)) {
                    long graceDue = record.optLong("grace_due_at", 0L);
                    if (graceDue > now + 1000L) {
                        try { scheduleGraceAlarm(context, record, graceDue); }
                        catch (Exception e) { MainApplication.log("CHECKUP", "restore grace failed: " + e.getMessage(), e); }
                    } else if (isWithinRestoreGrace(now, graceDue)) {
                        onGraceExpired(context, eventId);
                    } else {
                        expireRestoredRecord(context, record, now);
                    }
                } else if ("fired".equals(state)) {
                    long firedAt = record.optLong("fired_at", 0L);
                    if (isWithinRestoreGrace(now, firedAt)) {
                        // A short process restart may restore the visible notification,
                        // but must not ring or launch the full-screen activity again.
                        showAlarmNotification(context, record, true);
                    } else {
                        expireRestoredRecord(context, record, now);
                    }
                }
            }
        }
    }

    /** WS 重连后发送本地积压事件；只有收到后端 ACK 才移除。 */
    public static void flushPendingEvents(Context context, EventSender sender) {
        if (sender == null) return;
        synchronized (LOCK) {
            JSONArray queue = loadArray(context, KEY_EVENTS);
            JSONArray remaining = new JSONArray();
            for (int i = 0; i < queue.length(); i++) {
                JSONObject event = queue.optJSONObject(i);
                if (event == null) continue;
                try {
                    JSONObject msg = new JSONObject(event.toString());
                    msg.put("type", "checkup_alarm_event");
                    sender.send(msg);
                } catch (Exception ignored) {}
                // send() 只代表 OkHttp 接受了发送；必须等后端 ACK 才能移除。
                remaining.put(event);
            }
            saveArray(context, KEY_EVENTS, remaining);
        }
    }

    public static void ackEvent(Context context, String eventKey) {
        if (eventKey == null || eventKey.isEmpty()) return;
        synchronized (LOCK) {
            JSONArray queue = loadArray(context, KEY_EVENTS);
            JSONArray remaining = new JSONArray();
            for (int i = 0; i < queue.length(); i++) {
                JSONObject event = queue.optJSONObject(i);
                if (event == null || !eventKey.equals(event.optString("event_key", ""))) {
                    if (event != null) remaining.put(event);
                }
            }
            saveArray(context, KEY_EVENTS, remaining);
        }
    }

    private static void queueEvent(Context context, JSONObject record, String state, long occurredAt, long graceDueAt) {
        try {
            JSONObject event = new JSONObject();
            String eventId = record.optString(EXTRA_EVENT_ID, "");
            String occurredIso = isoTime(occurredAt);
            String eventKey = eventId + ":" + state + ":" + occurredAt;
            event.put("event_key", eventKey);
            event.put("event_id", eventId);
            event.put("state", state);
            event.put("occurred_at", occurredIso);
            event.put("trigger_at", record.optString("trigger_at_iso", ""));
            if (record.has(EXTRA_TRIGGER_AT)) event.put("trigger_at_ms", record.optLong(EXTRA_TRIGGER_AT, 0L));
            if (graceDueAt > 0) {
                event.put("grace_due_at", isoTime(graceDueAt));
                event.put("grace_due_at_ms", graceDueAt);
            }
            JSONArray queue = loadArray(context, KEY_EVENTS);
            boolean exists = false;
            for (int i = 0; i < queue.length(); i++) {
                JSONObject old = queue.optJSONObject(i);
                if (old != null && eventKey.equals(old.optString("event_key", ""))) {
                    exists = true;
                    break;
                }
            }
            if (!exists) queue.put(event);
            saveArray(context, KEY_EVENTS, queue);
            requestFlush(context);
        } catch (Exception e) {
            MainApplication.log("CHECKUP", "queue event failed: " + e.getMessage(), e);
        }
    }

    private static void requestFlush(Context context) {
        try {
            Intent intent = new Intent(context, MirrowRelayService.class)
                    .setAction(ACTION_FLUSH)
                    .setPackage(context.getPackageName());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent);
            else context.startService(intent);
        } catch (Exception e) {
            MainApplication.log("CHECKUP", "request flush failed: " + e.getMessage(), e);
        }
    }

    private static void showAlarmNotification(Context context, JSONObject record, boolean restoredSilently) {
        try {
            ensureAlarmChannel(context);
            String eventId = record.optString(EXTRA_EVENT_ID, "");
            String title = record.optString(EXTRA_TITLE, "MIRROW查岗");
            String body = record.optString(EXTRA_BODY, "查岗提醒");
            int requestCode = requestCode(eventId);
            PendingIntent fullScreen = activityPendingIntent(context, eventId, title, body, requestCode + 1);
            Intent dismissIntent = new Intent(context, CheckupAlarmDismissReceiver.class)
                    .setAction(ACTION_DISMISS).putExtra(EXTRA_EVENT_ID, eventId)
                    .setPackage(context.getPackageName());
            PendingIntent dismiss = PendingIntent.getBroadcast(
                    context, requestCode + 2, dismissIntent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            android.net.Uri sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                    .setContentTitle(title)
                    .setContentText(body)
                    .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
                    .setPriority(NotificationCompat.PRIORITY_MAX)
                    .setCategory(NotificationCompat.CATEGORY_ALARM)
                    .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                    .setOngoing(true)
                    .setAutoCancel(false)
                    .setContentIntent(fullScreen)
                    .setDeleteIntent(dismiss)
                    .addAction(new NotificationCompat.Action.Builder(
                            android.R.drawable.ic_media_pause, "按掉", dismiss).build());
            if (restoredSilently) {
                builder.setOnlyAlertOnce(true).setSilent(true);
            } else {
                builder.setSound(sound)
                        .setVibrate(new long[]{0, 600, 500, 600})
                        .setFullScreenIntent(fullScreen, true);
            }
            NotificationManagerCompat.from(context).notify(notificationId(eventId), builder.build());
        } catch (Exception e) {
            MainApplication.log("CHECKUP", "show alarm failed: " + e.getMessage(), e);
        }
    }

    private static void expireRestoredRecord(Context context, JSONObject record, long now) {
        try {
            record.put("state", "expired");
            record.put("expired_at", now);
            upsertRecord(context, record);
        } catch (Exception ignored) {}
        String eventId = record.optString(EXTRA_EVENT_ID, "");
        cancelNotification(context, eventId);
        queueEvent(context, record, "expired", now, 0L);
    }

    static boolean isWithinRestoreGrace(long now, long eventAt) {
        return eventAt > 0L && now >= eventAt && now - eventAt <= RESTORE_OVERDUE_GRACE_MS;
    }

    private static void ensureAlarmChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "MIRROW提醒闹钟", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("查岗复判后的手机闹钟");
        channel.enableVibration(true);
        channel.setVibrationPattern(new long[]{0, 600, 500, 600});
        channel.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        channel.setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build());
        nm.createNotificationChannel(channel);
    }

    private static String checkSchedulePermissions(Context context) {
        if (Build.VERSION.SDK_INT >= 31) {
            AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
            if (am == null || !am.canScheduleExactAlarms()) {
                return "精确闹钟权限未开启";
            }
        }
        if (Build.VERSION.SDK_INT >= 33
                && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return "手机通知权限未开启";
        }
        if (Build.VERSION.SDK_INT >= 34) {
            NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && !nm.canUseFullScreenIntent()) return "全屏闹钟通知权限未开启";
        }
        return "";
    }

    private static void scheduleOsAlarm(Context context, JSONObject record, long triggerAt) {
        String eventId = record.optString(EXTRA_EVENT_ID, "");
        int requestCode = requestCode(eventId);
        PendingIntent alarm = alarmPendingIntent(
                context, eventId, record.optString(EXTRA_TITLE, "MIRROW查岗"),
                record.optString(EXTRA_BODY, "查岗提醒"), triggerAt, requestCode);
        PendingIntent show = activityPendingIntent(
                context, eventId, record.optString(EXTRA_TITLE, "MIRROW查岗"),
                record.optString(EXTRA_BODY, "查岗提醒"), requestCode + 1);
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) throw new IllegalStateException("系统闹钟服务不可用");
        am.setAlarmClock(new AlarmManager.AlarmClockInfo(triggerAt, show), alarm);
    }

    private static void scheduleGraceAlarm(Context context, JSONObject record, long graceDue) {
        Intent intent = new Intent(context, CheckupGraceReceiver.class)
                .setAction(ACTION_GRACE_TRIGGER)
                .putExtra(EXTRA_EVENT_ID, record.optString(EXTRA_EVENT_ID, ""))
                .setPackage(context.getPackageName());
        PendingIntent pi = PendingIntent.getBroadcast(
                context, requestCode(record.optString(EXTRA_EVENT_ID, "")) + 3, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) throw new IllegalStateException("系统闹钟服务不可用");
        if (Build.VERSION.SDK_INT >= 23) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, graceDue, pi);
        else am.setExact(AlarmManager.RTC_WAKEUP, graceDue, pi);
    }

    private static void cancelOsAlarms(Context context, String eventId) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        int requestCode = requestCode(eventId);
        Intent alarmIntent = new Intent(context, CheckupAlarmReceiver.class).setPackage(context.getPackageName());
        PendingIntent alarm = PendingIntent.getBroadcast(
                context, requestCode, alarmIntent,
                PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
        if (alarm != null) {
            am.cancel(alarm);
            alarm.cancel();
        }
        Intent graceIntent = new Intent(context, CheckupGraceReceiver.class).setPackage(context.getPackageName());
        PendingIntent grace = PendingIntent.getBroadcast(
                context, requestCode + 3, graceIntent,
                PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
        if (grace != null) {
            am.cancel(grace);
            grace.cancel();
        }
        cancelNotification(context, eventId);
    }

    private static PendingIntent alarmPendingIntent(Context context, String eventId, String title,
                                                    String body, long triggerAt, int requestCode) {
        Intent intent = new Intent(context, CheckupAlarmReceiver.class)
                .setAction(ACTION_ALARM_TRIGGER)
                .putExtra(EXTRA_EVENT_ID, eventId)
                .putExtra(EXTRA_TITLE, title)
                .putExtra(EXTRA_BODY, body)
                .putExtra(EXTRA_TRIGGER_AT, triggerAt)
                .setPackage(context.getPackageName());
        return PendingIntent.getBroadcast(context, requestCode, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent activityPendingIntent(Context context, String eventId, String title,
                                                       String body, int requestCode) {
        Intent intent = new Intent(context, CheckupAlarmActivity.class)
                .putExtra(EXTRA_EVENT_ID, eventId)
                .putExtra(EXTRA_TITLE, title)
                .putExtra(EXTRA_BODY, body)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .setPackage(context.getPackageName());
        return PendingIntent.getActivity(context, requestCode, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static void cancelNotification(Context context, String eventId) {
        try { NotificationManagerCompat.from(context).cancel(notificationId(eventId)); }
        catch (Exception ignored) {}
    }

    private static void markAlarmFailure(Context context, JSONObject record, String error) {
        try {
            record.put("state", "alarm_failed");
            record.put("error", error == null ? "恢复闹钟失败" : error);
            upsertRecord(context, record);
            queueEvent(context, record, "alarm_failed", System.currentTimeMillis(), 0L);
        } catch (Exception ignored) {}
    }

    private static JSONObject success(JSONObject record, String content) {
        JSONObject result = new JSONObject();
        try {
            result.put("success", true);
            result.put("content", content);
            if (record != null) {
                result.put("event_id", record.optString(EXTRA_EVENT_ID, ""));
                result.put("state", record.optString("state", ""));
                result.put("alarm_id", record.optString("alarm_id", ""));
            }
        } catch (Exception ignored) {}
        return result;
    }

    private static JSONObject failure(String error) {
        JSONObject result = new JSONObject();
        try {
            result.put("success", false);
            result.put("error", error);
            result.put("content", error);
        } catch (Exception ignored) {}
        return result;
    }

    private static JSONObject findRecord(Context context, String eventId) {
        if (eventId == null || eventId.isEmpty()) return null;
        JSONArray records = loadArray(context, KEY_RECORDS);
        for (int i = 0; i < records.length(); i++) {
            JSONObject record = records.optJSONObject(i);
            if (record != null && eventId.equals(record.optString(EXTRA_EVENT_ID, ""))) return record;
        }
        return null;
    }

    private static void upsertRecord(Context context, JSONObject record) {
        JSONArray records = loadArray(context, KEY_RECORDS);
        String eventId = record.optString(EXTRA_EVENT_ID, "");
        JSONArray next = new JSONArray();
        boolean replaced = false;
        for (int i = 0; i < records.length(); i++) {
            JSONObject old = records.optJSONObject(i);
            if (old == null) continue;
            if (eventId.equals(old.optString(EXTRA_EVENT_ID, ""))) {
                next.put(record);
                replaced = true;
            } else {
                next.put(old);
            }
        }
        if (!replaced) next.put(record);
        saveArray(context, KEY_RECORDS, next);
    }

    private static JSONArray loadArray(Context context, String key) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String raw = prefs.getString(key, "[]");
        try { return new JSONArray(raw); }
        catch (Exception e) { return new JSONArray(); }
    }

    private static void saveArray(Context context, String key, JSONArray array) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(key, array.toString()).apply();
    }

    private static int requestCode(String eventId) {
        int code = eventId == null ? 1 : (eventId.hashCode() & 0x0fffffff);
        return code == 0 ? 1 : code;
    }

    private static int notificationId(String eventId) {
        return requestCode(eventId);
    }

    private static String isoTime(long millis) {
        return new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.US).format(new Date(millis));
    }
}
