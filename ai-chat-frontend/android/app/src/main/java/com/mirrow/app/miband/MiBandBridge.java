package com.mirrow.app.miband;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import com.mirrow.app.BuildConfig;
import com.mirrow.app.network.BackendEndpointPool;
import org.json.JSONObject;
import okhttp3.OkHttpClient;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Shared background relay / foreground plugin adapter. Never accepts credentials remotely. */
public final class MiBandBridge {
    private static final OkHttpClient UPLOAD_CLIENT = new OkHttpClient.Builder()
        .callTimeout(20, TimeUnit.SECONDS).followRedirects(false).followSslRedirects(false).build();
    private MiBandBridge() {}
    public static MiBandRuntime attach(Context context) {
        Context app = context.getApplicationContext();
        MiBandRuntime runtime = MiBandRuntime.get(app);
        runtime.setSampleSink(new MiBandHealthUploader(UPLOAD_CLIENT,
            () -> BackendEndpointPool.getSelected(app, BuildConfig.MIRROW_BACKEND_FALLBACK_URL)));
        return runtime;
    }
    public static boolean permitted(Context context) {
        return Build.VERSION.SDK_INT < 31 || context.checkSelfPermission("android.permission.BLUETOOTH_CONNECT") == PackageManager.PERMISSION_GRANTED;
    }
    public static JSONObject status(Context context) throws Exception {
        MiBandRuntime runtime = MiBandRuntime.get(context);
        MiBandStatus s = runtime.status();
        MiBandPreferences.Settings config = runtime.settings();
        JSONObject out = new JSONObject();
        out.put("configured", s.configured).put("connected", s.connected).put("authenticated", s.authenticated)
            .put("state", s.state).put("realtime", s.realtime).put("syncing", s.syncing)
            .put("device_name", s.deviceName).put("battery", s.battery)
            .put("heart_rate", s.latestHeartRate).put("measured_at", s.latestHeartRateAt / 1000.0)
            .put("last_sync_at", s.lastSyncAt / 1000.0).put("next_sync_at", s.nextSyncAt / 1000.0)
            .put("error", s.error).put("auth_key_configured", runtime.authKeyConfigured())
            .put("day_interval", config.dayIntervalMinutes).put("night_interval", config.nightIntervalMinutes)
            .put("night_start", MiBandPreferences.formatClock(config.nightStartMinute))
            .put("night_end", MiBandPreferences.formatClock(config.nightEndMinute));
        return out;
    }
    public static JSONObject execute(Context context, JSONObject params) throws Exception {
        String action = params.optString("action", "status");
        MiBandRuntime runtime = attach(context);
        if (action.equals("status")) return new JSONObject().put("success", true).put("data", status(context));
        if (!permitted(context)) throw new IllegalStateException("请在手机小米手环面板授权附近设备");
        if (!runtime.hasConfig()) throw new IllegalStateException("小米手环尚未在手机配置");
        switch (action) {
            case "connect": runtime.manualReconnect(); break;
            case "disconnect": runtime.disconnect(); break;
            case "sync": runtime.syncNow(); break;
            case "realtime_start": runtime.startRealtime(); break;
            case "realtime_stop": runtime.stopRealtime(); break;
            case "schedule":
                runtime.updateSchedule(new MiBandPreferences.Settings(params.getInt("day_interval"),
                    MiBandPreferences.parseClock(params.getString("night_start")),
                    MiBandPreferences.parseClock(params.getString("night_end")), params.getInt("night_interval")));
                break;
            case "vibrate": case "note":
                String pattern = params.optString("pattern", "single");
                if (!pattern.equals("single") && !pattern.equals("call")) throw new IllegalArgumentException("震动模式无效");
                CountDownLatch done = new CountDownLatch(1);
                AtomicBoolean success = new AtomicBoolean(false);
                MiBandRuntime.Completion completed = ok -> { success.set(ok); done.countDown(); };
                if (!runtime.status().authenticated) throw new IllegalStateException("手环尚未连接认证，未发送");
                String note = action.equals("note") ? params.getString("note") : "";
                if (action.equals("note") && (note.trim().isEmpty() || note.codePointCount(0, note.length()) > 80)) throw new IllegalArgumentException("纸条需1至80字");
                String commandId = params.optString("command_id", java.util.UUID.randomUUID().toString());
                long expires = params.optLong("expires_at_ms", System.currentTimeMillis()+25_000L);
                if (expires <= System.currentTimeMillis() || expires > System.currentTimeMillis()+60_000L) throw new IllegalArgumentException("手环指令已过期或有效期无效");
                if (!claim(context, commandId)) return new JSONObject().put("success", false).put("content", "同一手环指令已提交过，未重复执行；上次结果可能未知");
                runtime.touch(pattern, note, expires, completed);
                boolean finished = done.await(25, TimeUnit.SECONDS);
                return new JSONObject().put("success", finished && success.get())
                    .put("content", finished ? (success.get() ? "手环蓝牙写入已确认；不代表人类伙伴已看到" : "手环发送失败") : "手环执行回执超时，结果未知；不要自动重试")
                    .put("data", status(context));
            default: throw new IllegalArgumentException("不支持的手环操作");
        }
        return new JSONObject().put("success", true).put("content", "操作已提交，请查看手环状态确认结果").put("data", status(context));
    }

    private static synchronized boolean claim(Context context, String id) {
        if (!id.matches("[a-zA-Z0-9_-]{1,100}")) throw new IllegalArgumentException("指令标识无效");
        android.content.SharedPreferences prefs = context.getSharedPreferences("mirrow_band_receipts", Context.MODE_PRIVATE);
        if (prefs.contains(id)) return false;
        android.content.SharedPreferences.Editor edit = prefs.edit();
        long cutoff = System.currentTimeMillis()-86_400_000L;
        for (String key : prefs.getAll().keySet()) if (prefs.getLong(key, 0) < cutoff) edit.remove(key);
        // Durable reservation before queueing: a process crash cannot replay a tactile action.
        if (!edit.putLong(id, System.currentTimeMillis()).commit()) throw new IllegalStateException("无法保存指令凭据，未执行");
        return true;
    }
}
