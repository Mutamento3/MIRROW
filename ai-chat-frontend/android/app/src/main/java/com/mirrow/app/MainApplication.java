package com.mirrow.app;

import android.app.Application;
import android.util.Log;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** 日志 + 崩溃捕获 + 无障碍自愈 */
public class MainApplication extends Application {

    // ── 持久化日志（Download/mirrow_log.txt）──
    public static void log(String tag, String msg, Throwable e) {
        try {
            File dir = android.os.Environment.getExternalStoragePublicDirectory(
                android.os.Environment.DIRECTORY_DOWNLOADS);
            if (dir == null || !dir.exists()) dir = new File("/storage/emulated/0/Download");
            File f = new File(dir, "mirrow_log.txt");
            FileWriter w = new FileWriter(f, true);
            PrintWriter p = new PrintWriter(w);
            p.println("[" + new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date())
                + "] " + tag + " " + msg);
            if (e != null) e.printStackTrace(p);
            p.flush(); w.close();
        } catch (Exception ignored) {}
    }

    static {
        final Thread.UncaughtExceptionHandler original = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            log("FATAL", "Thread=" + t.getName(), e);
            Log.e("MIRROW", "FATAL on " + t.getName(), e);
            // 交给系统默认处理（杀进程）
            if (original != null) original.uncaughtException(t, e);
        });
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Log.i("MIRROW", "App onCreate");
        log("STARTUP", "MIRROW started", null);

        // 无障碍自愈：延迟 8s（冷启动时系统绑定无障碍是异步的，立即查必为 false 会误触发 toggle cycle）
        try {
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                try {
                    String r = MirrowAccessibilityService.ensureEnabled(MainApplication.this);
                    log("A11Y", "startup ensureEnabled: " + r, null);
                } catch (Throwable e) {
                    log("A11Y", "startup ensureEnabled crash: " + e.getMessage(), e);
                }
            }, 8000);
        } catch (Throwable ignored) {}
    }
}
