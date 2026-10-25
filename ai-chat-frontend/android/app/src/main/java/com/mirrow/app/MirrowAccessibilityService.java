package com.mirrow.app;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Bitmap;
import android.graphics.ColorSpace;
import android.hardware.HardwareBuffer;
import android.os.Build;
import android.provider.Settings;
import android.util.Base64;
import android.util.Log;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.inputmethod.InputMethodInfo;
import android.view.inputmethod.InputMethodManager;

import androidx.annotation.RequiresApi;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * MIRROW 无障碍截图服务 — 用 AccessibilityService.takeScreenshot()（API 30+）截取手机屏幕，
 * 无需额外 shell 权限或每次弹窗，只需用户在「设置 → 无障碍」里一次性开启本服务。
 *
 * 参照 AionsHome AionAccessibilityService，适配 MIRROW relay：
 * MobileToolPlugin / MirrowRelayService 通过 getInstance().captureScreenshotBase64() 同步取图。
 */
public class MirrowAccessibilityService extends AccessibilityService {

    private static final String TAG = "MirrowA11y";
    private static volatile MirrowAccessibilityService sInstance;

    // ── 截图节流 + 错误码（2026-08-22 根治"缓存帧"失败） ──
    /** 上次成功/尝试截图的墙钟时间，用于 1 秒频率限制节流（takeScreenshot 两次间隔 <1s 会 ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT）。 */
    private static final AtomicLong sLastShotTs = new AtomicLong(0);
    /** 两次截图的最小间隔——稍低于 1s 系统限制，留余量 */
    private static final long SHOT_MIN_INTERVAL_MS = 800;
    /** 最近一次截图失败的错误码/描述（成功时置空），供 relay handler 回传诊断 */
    private static volatile String sLastShotError = "";

    /** 内容感知 hash：跨 base64 前/中/尾多点采样，避开 JPEG 固定文件头（SOI/DQT/DHT）。
     *  旧实现取前 200 字符，落在固定文件头区，不同屏幕内容也生成相同 hash → 每次截图都被误判"缓存"。 */
    public static String screenshotContentHash(String b64) {
        int len = b64.length();
        if (len <= 256) return Integer.toString(b64.hashCode());
        StringBuilder sb = new StringBuilder(192);
        sb.append(b64, 0, 64);
        sb.append(b64, len / 2 - 32, len / 2 + 32);
        sb.append(b64, len - 64, len);
        return Integer.toString(sb.toString().hashCode());
    }

    /** 最近一次截图失败原因（成功为空串）。供 relay handler 在失败文案中回传，区分根因。 */
    public static String getLastShotError() {
        return sLastShotError;
    }

    // ── 音乐耳蜗：通知栏歌曲信息 ──
    private static volatile String sLastSongTitle = "";
    private static volatile String sLastSongArtist = "";
    private static volatile long sLastSongTime = 0;
    /** 获取最近一次从通知栏抓到的歌曲信息。用于 mobile_now_playing relay 工具。 */
    public static String[] getLastSongInfo() {
        if (System.currentTimeMillis() - sLastSongTime < 120_000) { // 2分钟有效
            return new String[] { sLastSongTitle, sLastSongArtist };
        }
        return null;
    }

    // ── 前台内容应用与覆盖窗口 ──
    // 输入法是宿主 App 上的覆盖层，不能把它当成独立的当前活动。
    private static final ForegroundWindowState sForegroundWindow = new ForegroundWindowState();
    /** 最近一次可信的非输入法内容应用。空串表示无/需回退 UsageEvents。 */
    public static String getLastWindowPackage() {
        return sForegroundWindow.getContentPackage();
    }

    public static String getObservedWindowPackage() {
        return sForegroundWindow.getObservedWindowPackage();
    }

    public static String getForegroundOverlayPackage() {
        return sForegroundWindow.getOverlayPackage();
    }

    public static long getLastWindowObservedAtMs() {
        return sForegroundWindow.getObservedAtMs();
    }

    public static long getLastContentObservedAtMs() {
        return sForegroundWindow.getContentObservedAtMs();
    }

    public static void clearLastWindowState() {
        sForegroundWindow.clear();
    }

    /** 当前包是否属于设备上的输入法。精确查询系统配置，不靠包名前缀猜测。 */
    public static boolean isInputMethodPackage(android.content.Context context, String pkg) {
        if (context == null || pkg == null || pkg.isEmpty()) return false;
        try {
            String component = Settings.Secure.getString(
                context.getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD);
            if (component != null) {
                android.content.ComponentName name = android.content.ComponentName.unflattenFromString(component);
                if (name != null && pkg.equals(name.getPackageName())) return true;
            }
        } catch (Throwable ignored) {}
        try {
            InputMethodManager imm = (InputMethodManager) context.getSystemService(
                android.content.Context.INPUT_METHOD_SERVICE);
            if (imm != null) {
                for (InputMethodInfo info : imm.getInputMethodList()) {
                    if (pkg.equals(info.getPackageName())) return true;
                }
            }
        } catch (Throwable ignored) {}
        return false;
    }

    /** 过滤系统 UI/桌面包名——这些窗口事件会误报前台。MirrowNotificationListener 复用。 */
    public static boolean isSystemUiPackage(String pkg) {
        if (pkg == null) return true;
        String p = pkg.toLowerCase();
        return p.startsWith("com.android.systemui")
            || p.startsWith("com.android.settings")
            || p.startsWith("com.android.launcher")
            || p.contains("launcher")
            || p.startsWith("com.coloros.")
            || p.startsWith("com.oplus.");
    }

    public static MirrowAccessibilityService getInstance() {
        return sInstance;
    }

    /** 服务是否已连接（用户已在无障碍设置里开启）。 */
    public static boolean isRunning() {
        return sInstance != null;
    }

    /**
     * 自愈：把本服务写回 enabled_accessibility_services（需 WRITE_SECURE_SETTINGS，adb pm grant 一次性授予）。
     *
     * 覆盖两种病：
     * 1. 授权真丢了（不在列表）→ 追加写回；
     * 2. 在列表但没绑定——进程崩溃后系统把服务挂进 Crashed services 不再重绑（2026-07-16 实锤：
     *    dataSync FGS 6h 配额杀进程连坐），原值重写不触发重绑 → 摘除→300ms 后写回（toggle cycle）。
     *
     * @return "already_enabled" | "healed_added" | "healed_rebind" | "no_permission" | "error:..."
     */
    public static String ensureEnabled(android.content.Context ctx) {
        try {
            if (ctx.checkSelfPermission(android.Manifest.permission.WRITE_SECURE_SETTINGS)
                    != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                return "no_permission";
            }
            final android.content.ContentResolver cr = ctx.getContentResolver();
            android.content.ComponentName me =
                new android.content.ComponentName(ctx, MirrowAccessibilityService.class);
            String full = me.flattenToString();            // com.mirrow.app/com.mirrow.app.MirrowAccessibilityService
            String shortForm = me.flattenToShortString();  // com.mirrow.app/.MirrowAccessibilityService
            String enabled = android.provider.Settings.Secure.getString(cr,
                android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (enabled == null) enabled = "";

            boolean listed = false;
            StringBuilder others = new StringBuilder();  // 其他服务，不 clobber
            for (String item : enabled.split(":")) {
                if (item.isEmpty()) continue;
                if (item.equals(full) || item.equals(shortForm)) { listed = true; continue; }
                if (others.length() > 0) others.append(':');
                others.append(item);
            }

            if (listed && isRunning()) return "already_enabled";

            final String withMe = others.length() > 0 ? others + ":" + full : full;
            if (listed) {
                // 摘除 → 延迟写回，两次独立的设置变更强制 AccessibilityManagerService 重绑
                android.provider.Settings.Secure.putString(cr,
                    android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, others.toString());
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                    try {
                        android.provider.Settings.Secure.putString(cr,
                            android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, withMe);
                        android.provider.Settings.Secure.putString(cr,
                            android.provider.Settings.Secure.ACCESSIBILITY_ENABLED, "1");
                    } catch (Throwable t) {
                        MainApplication.log("A11Y", "rebind write-back failed: " + t.getMessage(), t);
                    }
                }, 300);
                MainApplication.log("A11Y", "ensureEnabled: healed_rebind (toggle cycle)", null);
                return "healed_rebind";
            } else {
                android.provider.Settings.Secure.putString(cr,
                    android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, withMe);
                android.provider.Settings.Secure.putString(cr,
                    android.provider.Settings.Secure.ACCESSIBILITY_ENABLED, "1");
                MainApplication.log("A11Y", "ensureEnabled: healed_added", null);
                return "healed_added";
            }
        } catch (Throwable t) {
            MainApplication.log("A11Y", "ensureEnabled failed: " + t.getMessage(), t);
            return "error:" + t.getMessage();
        }
    }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        sInstance = this;
        Log.i(TAG, "MirrowAccessibilityService connected");
        // 扫描已有通知，抓取当前歌曲
        try {
            android.app.NotificationManager nm = (android.app.NotificationManager)
                getSystemService(NOTIFICATION_SERVICE);
            if (nm != null) {
                android.service.notification.StatusBarNotification[] active = nm.getActiveNotifications();
                if (active != null) {
                    for (android.service.notification.StatusBarNotification sbn : active) {
                        String pkg = sbn.getPackageName();
                        if (!"com.netease.cloudmusic".equals(pkg)) continue;
                        String title = sbn.getNotification().extras
                            .getString(android.app.Notification.EXTRA_TITLE, "");
                        if (title != null && title.contains(" - ")) {
                            int idx = title.indexOf(" - ");
                            sLastSongTitle = title.substring(0, idx).trim();
                            sLastSongArtist = title.substring(idx + 3).trim();
                            sLastSongTime = System.currentTimeMillis();
                            Log.i(TAG, "🎵 启动扫描到歌曲: " + sLastSongTitle + " - " + sLastSongArtist);
                            break;
                        }
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "启动扫描通知失败: " + e.getMessage());
        }
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        sInstance = null;
        Log.i(TAG, "MirrowAccessibilityService unbound");
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        sInstance = null;
        super.onDestroy();
    }

    // 无障碍事件 / 中断——本服务只用于截图，不处理事件
    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        // 前台窗口切换：输入法只记录为覆盖窗口，保留其下方的内容应用。
        if (event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            try {
                CharSequence pkg = event.getPackageName();
                if (pkg != null) {
                    String p = pkg.toString();
                    if (!isSystemUiPackage(p)) {
                        sForegroundWindow.observe(
                            p,
                            isInputMethodPackage(this, p),
                            System.currentTimeMillis());
                    }
                }
            } catch (Throwable ignored) {}
            return;
        }
        // 音乐耳蜗：从通知栏抓取歌曲信息
        if (event.getEventType() == AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED) {
            try {
                CharSequence text = event.getText() != null && !event.getText().isEmpty()
                    ? event.getText().get(0) : null;
                if (text == null) return;
                String title = text.toString().trim();
                if (title.isEmpty() || title.length() > 200) return;
                // 检查是否像歌曲标题：包含 " - " 分隔符
                int dashIdx = title.indexOf(" - ");
                if (dashIdx > 0 && dashIdx < title.length() - 2) {
                    sLastSongTitle = title.substring(0, dashIdx).trim();
                    sLastSongArtist = title.substring(dashIdx + 3).trim();
                    sLastSongTime = System.currentTimeMillis();
                    Log.d(TAG, "🎵 通知栏歌曲: " + sLastSongTitle + " - " + sLastSongArtist);
                }
            } catch (Exception ignored) {}
        }
    }

    @Override
    public void onInterrupt() {}

    /**
     * 同步截取屏幕并返回 JPEG base64（NO_WRAP）。阻塞等待回调，供 relay 的同步 handler 调用。
     * 失败 / 超时 / API<30 返回 null。
     *
     * @param maxDim      长边最大像素（降采样以控制 relay 体积）
     * @param jpegQuality JPEG 质量 0-100
     * @param timeoutMs   等待回调超时
     */
    public String captureScreenshotBase64(final int maxDim, final int jpegQuality, long timeoutMs) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            sLastShotError = "api_below_30";
            Log.w(TAG, "takeScreenshot 需要 Android 11+ (API 30)，当前 " + Build.VERSION.SDK_INT);
            return null;
        }
        // ⚡ 节流：takeScreenshot 两次调用间隔 <1s 会返回 ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT。
        //   mobile_vision/check_phone/eyes 多消费者并发时踩中 → 全部失败。等过 800ms 边界再截。
        long now = System.currentTimeMillis();
        long last = sLastShotTs.get();
        long waitMs = last + SHOT_MIN_INTERVAL_MS - now;
        if (waitMs > 0) {
            try { Thread.sleep(waitMs); } catch (InterruptedException ignored) {}
        }
        sLastShotTs.set(System.currentTimeMillis());
        return doCapture(maxDim, jpegQuality, timeoutMs);
    }

    @RequiresApi(api = Build.VERSION_CODES.R)
    private String doCapture(final int maxDim, final int jpegQuality, long timeoutMs) {
        final CountDownLatch latch = new CountDownLatch(1);
        final String[] holder = new String[1];
        sLastShotError = "";
        try {
            takeScreenshot(Display.DEFAULT_DISPLAY, getMainExecutor(),
                new TakeScreenshotCallback() {
                    @Override
                    public void onSuccess(ScreenshotResult screenshot) {
                        Bitmap bmp = null;
                        try {
                            HardwareBuffer buffer = screenshot.getHardwareBuffer();
                            ColorSpace cs = screenshot.getColorSpace();
                            Bitmap wrapped = Bitmap.wrapHardwareBuffer(buffer, cs);
                            if (wrapped != null) {
                                bmp = wrapped.copy(Bitmap.Config.ARGB_8888, false);
                                wrapped.recycle();
                            }
                            try { buffer.close(); } catch (Throwable ignore) {}
                            if (bmp != null) {
                                int w = bmp.getWidth(), h = bmp.getHeight();
                                float scale = Math.min(1.0f, (float) maxDim / Math.max(w, h));
                                if (scale < 1.0f) {
                                    Bitmap scaled = Bitmap.createScaledBitmap(
                                        bmp, (int) (w * scale), (int) (h * scale), true);
                                    bmp.recycle();
                                    bmp = scaled;
                                }
                                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                                bmp.compress(Bitmap.CompressFormat.JPEG, jpegQuality, bos);
                                holder[0] = Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP);
                                sLastShotError = "";  // 成功清错误
                            }
                        } catch (Throwable t) {
                            Log.w(TAG, "screenshot encode failed: " + t.getMessage());
                            sLastShotError = "encode_error";
                        } finally {
                            if (bmp != null) bmp.recycle();
                            latch.countDown();
                        }
                    }

                    @Override
                    public void onFailure(int errorCode) {
                        Log.w(TAG, "takeScreenshot failed: errorCode=" + errorCode);
                        // AOSP @hide 错误码（未公开于 android.jar，值稳定）：
                        //   1=INTERNAL_ERROR 2=INTERVAL_TIME_SHORT 3=INVALID_DISPLAY 4=NO_ACCESSIBILITY_ACCESS
                        if (errorCode == 2) {
                            sLastShotError = "interval_limit";
                        } else if (errorCode == 4) {
                            sLastShotError = "no_accessibility_access";
                            // 权限问题 → 触发自愈，下次截图可恢复
                            try { ensureEnabled(getApplicationContext()); } catch (Throwable ignored) {}
                        } else {
                            sLastShotError = "error_" + errorCode;
                        }
                        latch.countDown();
                    }
                });
            latch.await(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (Throwable t) {
            Log.w(TAG, "captureScreenshot exception: " + t.getMessage());
            sLastShotError = "exception";
        }
        return holder[0];
    }
}
