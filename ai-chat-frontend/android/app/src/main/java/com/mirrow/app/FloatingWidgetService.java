package com.mirrow.app;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.TextView;
import android.widget.Toast;

import com.mirrow.app.plugins.MobileToolPlugin;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 刷手机模式悬浮球 — 前台服务 + WindowManager 悬浮窗。
 *
 * 参考 recruit-lifestyle/FloatingView 的 ChatHead 模式，精简为单圆形按钮。
 * 仅后台显示，MIRROW 前台时由前端调用 hide_floating_widget 停止此服务。
 *
 * 触摸：拖拽移动位置，点击（位移 < 20dp）→ HTTP POST 召唤后端截图分析。
 */
public class FloatingWidgetService extends Service {
    private static final String TAG = "MIRROW_FloatingWidget";
    private static final String CHANNEL_ID = "floating_widget_channel";
    private static final int NOTIFICATION_ID = 2002;

    private WindowManager windowManager;
    private View floatingView;
    private WindowManager.LayoutParams params;

    // Keep-alive locks
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    // 触摸追踪（区分拖拽 vs 点击）
    private float initialX, initialY;
    private float touchStartX, touchStartY;
    private static final int CLICK_THRESHOLD_DP = 20;

    @Override
    public void onCreate() {
        super.onCreate();
        Log.i(TAG, "Service created");
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        createNotificationChannel();

        // WakeLock: keep CPU alive when screen is off
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, TAG + ":WakeLock");
            wakeLock.acquire(10 * 60 * 1000L /* 10 minutes, auto-release */);
            Log.i(TAG, "WakeLock acquired");
        }

        // WifiLock: keep WiFi alive in standby
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm != null) {
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, TAG + ":WifiLock");
            wifiLock.acquire();
            Log.i(TAG, "WifiLock acquired");
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Log.i(TAG, "onStartCommand — showing floating bubble");

        // 前台通知
        Intent appIntent = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this, 0, appIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0)
        );

        Notification notification = new Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("MIRROW 陪刷中")
            .setContentText("正在陪你刷手机，点此回到聊天")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build();

        startForeground(NOTIFICATION_ID, notification);

        // 如果已有悬浮窗先移除
        if (floatingView != null) {
            try { windowManager.removeView(floatingView); } catch (Exception ignored) {}
        }

        // 创建悬浮球 View
        floatingView = createFloatingBubble();

        // WindowManager 参数
        int layoutType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            : WindowManager.LayoutParams.TYPE_PHONE;

        params = new WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            layoutType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        );
        params.gravity = Gravity.TOP | Gravity.START;
        params.x = dpToPx(16);
        params.y = dpToPx(200);

        windowManager.addView(floatingView, params);

        return START_STICKY;
    }

    private View createFloatingBubble() {
        // 圆形 TextView，显示 📢
        TextView bubble = new TextView(this);
        bubble.setText("📢");  // 📢 emoji
        bubble.setTextSize(24);
        bubble.setGravity(Gravity.CENTER);

        int size = dpToPx(60);
        // 用 GradientDrawable 做圆形紫色背景
        android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
        bg.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        bg.setColor(0xB3_88FF_CC);  // 半透明紫色
        bg.setStroke(dpToPx(2), 0xFF_B388FF);
        bubble.setBackground(bg);

        // 触摸处理
        bubble.setOnTouchListener(new View.OnTouchListener() {
            @Override
            public boolean onTouch(View v, MotionEvent event) {
                switch (event.getAction()) {
                    case MotionEvent.ACTION_DOWN:
                        initialX = params.x;
                        initialY = params.y;
                        touchStartX = event.getRawX();
                        touchStartY = event.getRawY();
                        return true;

                    case MotionEvent.ACTION_MOVE:
                        params.x = (int) (initialX + (event.getRawX() - touchStartX));
                        params.y = (int) (initialY + (event.getRawY() - touchStartY));
                        windowManager.updateViewLayout(floatingView, params);
                        return true;

                    case MotionEvent.ACTION_UP:
                        v.setAlpha(1f);  // 恢复透明度
                        float dx = Math.abs(event.getRawX() - touchStartX);
                        float dy = Math.abs(event.getRawY() - touchStartY);
                        int threshold = dpToPx(CLICK_THRESHOLD_DP);
                        if (dx < threshold && dy < threshold) {
                            onBubbleClick();  // 点击 → 召唤
                        }
                        return true;

                    case MotionEvent.ACTION_CANCEL:
                        v.setAlpha(1f);  // 恢复透明度
                        return true;
                }
                return false;
            }
        });

        return bubble;
    }

    private void onBubbleClick() {
        Log.i(TAG, "Bubble clicked — summoning backend via HTTP");

        // 振动反馈（50ms 轻振）
        try {
            android.os.Vibrator vib = (android.os.Vibrator) getSystemService(VIBRATOR_SERVICE);
            if (vib != null && vib.hasVibrator()) {
                vib.vibrate(android.os.VibrationEffect.createOneShot(50, android.os.VibrationEffect.DEFAULT_AMPLITUDE));
            }
        } catch (Exception e) { /* ignore */ }

        // 视觉闪动反馈
        if (floatingView != null) {
            floatingView.animate().scaleX(0.85f).scaleY(0.85f).setDuration(100)
                .withEndAction(() -> floatingView.animate().scaleX(1f).scaleY(1f).setDuration(100).start())
                .start();
        }

        // 从 SharedPreferences 读取后端 URL（JS 启动时通过 write_backend_url 写入）
        final String backendUrl = MobileToolPlugin.getBackendUrl(this);
        Log.i(TAG, "Summon backend URL: " + backendUrl);

        // 后台线程发起 HTTP POST
        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                URL url = new URL(backendUrl + "/api/phone-browse/summon");
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(30000);
                conn.setDoOutput(true);

                OutputStream os = conn.getOutputStream();
                os.write("{}".getBytes("UTF-8"));
                os.flush();
                os.close();

                int code = conn.getResponseCode();
                Log.i(TAG, "Summon response: " + code);

                // 读取响应体
                String body = "";
                if (code == 200) {
                    java.io.BufferedReader reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(conn.getInputStream()));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    body = sb.toString();
                    Log.i(TAG, "Summon body: " + body);
                }

                // 解析 JSON 响应获取 ok/error
                String toastMsg = buildSummonToast(body, code);

                new Handler(getMainLooper()).post(() ->
                    Toast.makeText(FloatingWidgetService.this, toastMsg, Toast.LENGTH_SHORT).show());
            } catch (Exception e) {
                Log.w(TAG, "Summon HTTP failed: " + e.getMessage());
                final String errMsg = e.getMessage() != null ? e.getMessage() : "未知网络错误";
                new Handler(getMainLooper()).post(() ->
                    Toast.makeText(FloatingWidgetService.this, "召唤失败：" + errMsg, Toast.LENGTH_SHORT).show());
            } finally {
                if (conn != null) conn.disconnect();
            }
        }).start();
    }

    @Override
    public void onDestroy() {
        Log.i(TAG, "Service destroyed — removing bubble");
        if (floatingView != null) {
            try { windowManager.removeView(floatingView); } catch (Exception ignored) {}
            floatingView = null;
        }
        // Release keep-alive locks
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
            Log.i(TAG, "WakeLock released");
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
            Log.i(TAG, "WifiLock released");
        }
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        Log.i(TAG, "onTaskRemoved — scheduling restart in 3s");
        // Self-revival: when user swipes away the app, restart in 3 seconds
        Intent restartIntent = new Intent(getApplicationContext(), FloatingWidgetService.class);
        PendingIntent pendingIntent = PendingIntent.getService(
            getApplicationContext(), 0, restartIntent,
            PendingIntent.FLAG_ONE_SHOT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0)
        );
        AlarmManager alarmManager = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (alarmManager != null) {
            long triggerTime = System.currentTimeMillis() + 3000; // 3 seconds
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerTime, pendingIntent);
            } else {
                alarmManager.setExact(AlarmManager.RTC_WAKEUP, triggerTime, pendingIntent);
            }
            Log.i(TAG, "Restart scheduled at " + triggerTime);
        }
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "MIRROW 陪刷",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("刷手机模式悬浮球运行中");
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    private String buildSummonToast(String body, int httpCode) {
        try {
            org.json.JSONObject json = new org.json.JSONObject(body);
            if (json.optBoolean("ok", false)) {
                return "📢 已召唤 AI 来看你的屏幕";
            } else {
                return "召唤失败：" + json.optString("error", "未知错误");
            }
        } catch (Exception e) {
            return (httpCode == 200) ? "📢 已召唤 AI 来看你的屏幕" : "召唤失败（" + httpCode + "）";
        }
    }

    private int dpToPx(int dp) {
        float density = getResources().getDisplayMetrics().density;
        return (int) (dp * density + 0.5f);
    }

    /**
     * 静态工具方法：检查悬浮窗权限是否已授予
     */
    public static boolean hasOverlayPermission(android.content.Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(context);
        }
        return true;  // Android < 6.0 无此限制
    }

    /**
     * 静态工具方法：打开悬浮窗权限设置页
     */
    public static void openOverlayPermissionSettings(android.content.Context context) {
        Intent intent = new Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:" + context.getPackageName())
        );
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }
}
