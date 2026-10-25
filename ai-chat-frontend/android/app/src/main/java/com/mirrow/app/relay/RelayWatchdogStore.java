package com.mirrow.app.relay;

import android.content.Context;
import android.content.SharedPreferences;

/** Small persistent record of relay liveness. No network or location data is stored here. */
public final class RelayWatchdogStore {
    private static final String PREFS = "relay_watchdog";
    private static final String KEY_INSTANCE_ID = "service_instance_id";
    private static final String KEY_SERVICE_STARTED_AT = "service_started_at";
    private static final String KEY_LAST_WS_OPEN_AT = "last_ws_open_at";
    private static final String KEY_LAST_SERVER_ACK_AT = "last_server_ack_at";
    private static final String KEY_LAST_DISCONNECT_AT = "last_disconnect_at";
    private static final String KEY_LAST_DISCONNECT_REASON = "last_disconnect_reason";
    private static final String KEY_LAST_TRIGGER_REASON = "last_trigger_reason";
    private static final String KEY_LAST_DATA_REQUEST_AT = "last_data_request_at";
    private static final String KEY_LAST_DATA_RESULT_AT = "last_data_result_at";
    private static final String KEY_NEXT_WATCHDOG_AT = "next_watchdog_at";
    private static final String KEY_EXACT_ALARM_AVAILABLE = "exact_alarm_available";

    private RelayWatchdogStore() {}

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static void serviceStarted(Context context, String instanceId) {
        prefs(context).edit()
            .putString(KEY_INSTANCE_ID, instanceId)
            .putLong(KEY_SERVICE_STARTED_AT, System.currentTimeMillis())
            .commit();
    }

    public static String instanceId(Context context) {
        return prefs(context).getString(KEY_INSTANCE_ID, "");
    }

    public static void wsOpened(Context context) {
        prefs(context).edit().putLong(KEY_LAST_WS_OPEN_AT, System.currentTimeMillis()).apply();
    }

    public static void serverAcknowledged(Context context) {
        prefs(context).edit().putLong(KEY_LAST_SERVER_ACK_AT, System.currentTimeMillis()).apply();
    }

    public static long lastServerAckAt(Context context) {
        return prefs(context).getLong(KEY_LAST_SERVER_ACK_AT, 0L);
    }

    public static void dataRequested(Context context) {
        prefs(context).edit().putLong(KEY_LAST_DATA_REQUEST_AT, System.currentTimeMillis()).apply();
    }

    public static void dataResult(Context context) {
        prefs(context).edit().putLong(KEY_LAST_DATA_RESULT_AT, System.currentTimeMillis()).apply();
    }

    public static long lastDataRequestAt(Context context) {
        return prefs(context).getLong(KEY_LAST_DATA_REQUEST_AT, 0L);
    }

    public static long lastDataResultAt(Context context) {
        return prefs(context).getLong(KEY_LAST_DATA_RESULT_AT, 0L);
    }

    public static void disconnected(Context context, String reason) {
        prefs(context).edit()
            .putLong(KEY_LAST_DISCONNECT_AT, System.currentTimeMillis())
            .putString(KEY_LAST_DISCONNECT_REASON, sanitizeReason(reason))
            .apply();
    }

    public static void triggered(Context context, String reason) {
        prefs(context).edit().putString(KEY_LAST_TRIGGER_REASON, sanitizeReason(reason)).apply();
    }

    public static long nextWatchdogAt(Context context) {
        return prefs(context).getLong(KEY_NEXT_WATCHDOG_AT, 0L);
    }

    public static void nextWatchdogAt(Context context, long atMillis, boolean exactAvailable) {
        prefs(context).edit()
            .putLong(KEY_NEXT_WATCHDOG_AT, atMillis)
            .putBoolean(KEY_EXACT_ALARM_AVAILABLE, exactAvailable)
            .apply();
    }

    private static String sanitizeReason(String value) {
        if (value == null) return "unknown";
        String compact = value.replace('\n', ' ').replace('\r', ' ');
        return compact.length() > 80 ? compact.substring(0, 80) : compact;
    }
}
