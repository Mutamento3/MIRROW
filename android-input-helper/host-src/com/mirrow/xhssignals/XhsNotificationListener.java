package com.mirrow.xhssignals;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;

/** Only XHS may enter the journal. Notification content is hashed, never retained or emitted. */
public final class XhsNotificationListener extends NotificationListenerService {
    private static final String TARGET = "com.xingin.xhs";
    private static volatile XhsNotificationListener current;
    private volatile boolean connected;

    @Override public void onListenerConnected() {
        connected = true;
        current = this;
        readAvailability(this);
    }

    @Override public void onListenerDisconnected() {
        connected = false;
        if (current == this) current = null;
        SignalJournal.get(this).gap();
    }

    @Override public void onDestroy() {
        connected = false;
        if (current == this) current = null;
        SignalJournal.get(this).gap();
        super.onDestroy();
    }

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        // Reject other packages before touching Notification or its extras.
        if (sbn == null || !TARGET.equals(sbn.getPackageName())) return;
        record(sbn);
    }

    @Override public void onNotificationRemoved(StatusBarNotification sbn) {
        // Removal does not mean AI read or handled its source comment. Keep pending signals.
    }

    static String readAvailability(android.content.Context context) {
        return readAvailability(context, true);
    }

    static String readAvailability(android.content.Context context, boolean collectActive) {
        SignalJournal journal = SignalJournal.get(context);
        XhsNotificationListener listener = current;
        if (listener == null || !listener.connected) {
            journal.ensureGap();
            return "disconnected";
        }
        try {
            // A current successful OS query is required: persisted connected flags are forbidden.
            StatusBarNotification[] active = listener.getActiveNotifications();
            if (active == null || current != listener || !listener.connected) {
                journal.ensureGap();
                return "unknown";
            }
            if (collectActive) for (StatusBarNotification sbn : active) {
                if (sbn != null && TARGET.equals(sbn.getPackageName())) listener.record(sbn);
            }
            if (current != listener || !listener.connected) {
                journal.ensureGap();
                return "unknown";
            }
            return "connected";
        } catch (Exception ignored) {
            journal.ensureGap();
            return "unknown";
        }
    }

    private void record(StatusBarNotification sbn) {
        try {
            // Defense in depth for both callback and active-snapshot paths.
            if (!TARGET.equals(sbn.getPackageName())) return;
            Notification notification = sbn.getNotification();
            Bundle extras = notification == null ? null : notification.extras;
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            String[] keys = {"android.title", "android.text", "android.bigText", "android.subText",
                "android.summaryText", "android.infoText", "android.conversationTitle"};
            for (String key : keys) {
                update(digest, key);
                CharSequence value = extras == null ? null : extras.getCharSequence(key);
                update(digest, value == null ? "" : value.toString());
            }
            CharSequence[] lines = extras == null ? null : extras.getCharSequenceArray("android.textLines");
            update(digest, Integer.toString(lines == null ? 0 : lines.length));
            if (lines != null) for (CharSequence line : lines) update(digest, line == null ? "" : line.toString());
            StringBuilder hash = new StringBuilder(64);
            for (byte b : digest.digest()) hash.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
            SignalJournal.get(this).observe(sbn.getKey(), sbn.getPostTime(), hash.toString());
        } catch (Exception ignored) {
            SignalJournal.get(this).gap();
        }
    }

    private static void update(MessageDigest digest, String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        digest.update((byte) (bytes.length >>> 24));
        digest.update((byte) (bytes.length >>> 16));
        digest.update((byte) (bytes.length >>> 8));
        digest.update((byte) bytes.length);
        digest.update(bytes);
    }
}

