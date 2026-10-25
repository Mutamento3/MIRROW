package com.mirrow.app;

import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Log;

/**
 * MIRROW 音乐耳蜗 — 通知监听器
 * 用户需在「设置 → 通知访问」里授权一次，之后自动抓取网易云等 App 的歌曲通知。
 *
 * 参考：@beatsphere/expo-music-detector, nowplaying(Flutter), flutter_media_controller
 */
public class MirrowNotificationListener extends NotificationListenerService {

    private static final String TAG = "MirrowNotifListener";

    // ── 支持的音乐 App 白名单 ──
    private static final String[] MUSIC_PACKAGES = {
        "com.netease.cloudmusic",    // 网易云音乐
        "com.tencent.qqmusic",       // QQ音乐
        "com.kugou.android",         // 酷狗音乐
        "com.spotify.music",         // Spotify
        "com.apple.android.music",   // Apple Music
    };

    // ── 通用通知缓存（2026-08-22 新增；2026-08-24 移植 AionsHome 噪声过滤 + 计数字段）──
    // Android notification key 是身份；读取时与系统当前活动通知重新校准。
    private static final NotificationSnapshotCache sNotifications = new NotificationSnapshotCache();
    private static volatile MirrowNotificationListener sListener;
    private static volatile Runnable sChangeCallback;
    private static final Object sNotifLock = new Object();

    // ── 噪声过滤（AionsHome NotificationContextPolicy 移植，2026-08-24）──
    /** 常驻/进度类 category → 噪声（除非 content 命中重要状态 marker） */
    private static final String[] NOISE_CATEGORIES = {"transport", "service", "progress"};
    /** 促销词：命中则「重要实时状态」判否（外卖通知里的红包文案不算重要） */
    private static final String[] PROMO_WORDS = {
        "红包", "优惠", "折扣", "促销", "大促", "领券", "购买", "下单更", "限时优惠",
        "coupon", "discount", "limited offer", "sale"
    };
    /** 重要实时状态 marker：外卖/快递/打车/航班等 → 保留 */
    private static final String[] IMPORTANT_MARKERS = {
        "外卖", "配送", "骑手", "骑士", "送达", "取餐",
        "快递", "包裹", "派送", "驿站",
        "打车", "司机", "网约车",
        "航班", "登机", "候机", "起飞", "延误",
        "delivery", "courier", "parcel", "driver", "taxi", "flight", "boarding"
    };

    /** 读取通用通知缓存，供 mobile_notifications relay 工具。返回数组 [pkg,title,text,timeMs,category,ongoing,count]。 */
    public static String[][] getRecentNotifications() {
        synchronized (sNotifLock) {
            if (!refreshActiveNotifications()) return null; // unknown, not empty/idle
            return sNotifications.recent(System.currentTimeMillis());
        }
    }

    /** 返回当前仍存在的 CATEGORY_CALL 通知来源包名，不把已移除的旧通知当作当前通话。 */
    public static String[] getActiveCallPackages() {
        synchronized (sNotifLock) {
            if (!refreshActiveNotifications()) return new String[0];
            return sNotifications.activeCallPackages();
        }
    }

    public static void setChangeCallback(Runnable callback) { sChangeCallback = callback; }

    private static void notifyChanged() {
        Runnable callback = sChangeCallback;
        if (callback != null) callback.run();
    }

    /** Rebuild from OS authority; no replay callbacks, new timestamps or music updates. */
    private static boolean refreshActiveNotifications() {
        MirrowNotificationListener listener = sListener;
        if (listener == null) return false;
        try {
            StatusBarNotification[] active = listener.getActiveNotifications();
            if (active == null) return false;
            sNotifications.clear();
            for (StatusBarNotification sbn : active) listener.cacheNotification(sbn);
            return true;
        } catch (Exception error) {
            sNotifications.clear();
            return false;
        }
    }

    private void cacheNotification(StatusBarNotification sbn) {
        if (sbn == null) return;
        String pkg = sbn.getPackageName();
        if (pkg == null || pkg.equals(getPackageName())
                || MirrowAccessibilityService.isSystemUiPackage(pkg)) return;
        android.app.Notification notification = sbn.getNotification();
        String category = safe(notification.category);
        android.os.Bundle extras = notification.extras;
        String title = safe(extras.getString("android.title", ""));
        String text = safe(extras.getString("android.bigText", ""));
        if (text.isEmpty()) text = safe(extras.getString("android.text", ""));
        boolean ongoing = (notification.flags & android.app.Notification.FLAG_ONGOING_EVENT) != 0;
        synchronized (sNotifLock) {
            // Changing a call into a missed-call/noise notification must also clear its old state.
            sNotifications.remove(sbn.getKey());
            if (!isNoise(category, ongoing, title, text)) {
                sNotifications.put(sbn.getKey(), pkg, title, text, sbn.getPostTime(), category, ongoing);
            }
        }
    }

    private static boolean isNoise(String category, boolean ongoing, String title, String text) {
        String kind = (category == null ? "" : category).toLowerCase();
        String content = (safe(title) + " " + safe(text)).toLowerCase();
        // 促销词 + 无重要 marker → 纯广告，直接丢弃（2026-08-24 人类伙伴需求：别浪费采集在广告上）
        if (hasPromo(content)) {
            return !isImportantLiveStatus(content);  // 外卖红包（促销+重要marker）仍保留
        }
        if (isImportantLiveStatus(content)) return false;  // 外卖/快递/打车/航班 → 重要，保留
        for (String c : NOISE_CATEGORIES) {
            if (c.equals(kind)) return true;               // 常驻/进度类 → 噪声
        }
        return ongoing && (content.contains("运行中") || content.contains("播放中")
                || content.contains("running") || content.contains("playing"));
    }

    private static boolean isImportantLiveStatus(String content) {
        for (String marker : IMPORTANT_MARKERS) {
            if (content.contains(marker)) return true;
        }
        return false;
    }

    private static boolean hasPromo(String content) {
        for (String promo : PROMO_WORDS) {
            if (content.contains(promo)) return true;
        }
        return false;
    }

    private static String safe(String s) { return s == null ? "" : s.trim(); }

    // ── 缓存的歌曲信息 ──
    private static volatile String sSongTitle = "";
    private static volatile String sSongArtist = "";
    private static volatile long sSongTime = 0;
    private static final long CACHE_TTL_MS = 120_000; // 2分钟有效

    /** 获取最近一次从通知栏抓到的歌曲信息。MirrowRelayService 轮询此方法。 */
    public static String[] getLastSong() {
        if (System.currentTimeMillis() - sSongTime < CACHE_TTL_MS
                && !sSongTitle.isEmpty()) {
            return new String[] { sSongTitle, sSongArtist };
        }
        return null;
    }

    /** 判断包名是否为支持的音乐 App */
    private static boolean isMusicPackage(String pkg) {
        for (String mp : MUSIC_PACKAGES) {
            if (mp.equals(pkg)) return true;
        }
        return false;
    }

    /** 自适应解析歌名-歌手分隔符（不同 App 格式不同） */
    private static String[] parseSongTitle(String title) {
        // 优先 " - "（网易云/QQ音乐），回退 " · "（Spotify），再回退 "—"
        String[][] separators = {
            {" - ", " - "},
            {" · ", " · "},
            {"—", "—"},
        };
        for (String[] sep : separators) {
            int idx = title.indexOf(sep[0]);
            if (idx > 0 && idx < title.length() - 2) {
                return new String[]{
                    title.substring(0, idx).trim(),
                    title.substring(idx + sep[0].length()).trim()
                };
            }
        }
        return null;
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            String pkg = sbn.getPackageName();
            // 跳过自己的保活/消息通知（否则 AI 会把"自己发的通知"当人类伙伴收到的通知）
            if (pkg == null || pkg.equals(getPackageName())) return;
            // 过滤系统 UI/桌面通知（2026-08-23：避免 AI 感知"系统更新/桌面"等无效通知）
            if (com.mirrow.app.MirrowAccessibilityService.isSystemUiPackage(pkg)) return;
            android.app.Notification notif = sbn.getNotification();
            String category = notif.category == null ? "" : notif.category;
            cacheNotification(sbn);
            notifyChanged();

            android.os.Bundle extras = notif.extras;
            String title = extras.getString("android.title", "");
            String text = extras.getString("android.text", "");
            // 大文本优先（AionsHome 用 EXTRA_BIG_TEXT——折叠通知的完整内容在 bigText）
            String bigText = extras.getString("android.bigText", "");
            if (title == null) title = "";
            if (text == null) text = "";
            if (bigText != null && !bigText.isEmpty() && !bigText.equals(text)) text = bigText;
            boolean ongoing = (notif.flags & android.app.Notification.FLAG_ONGOING_EVENT) != 0;

            // 噪声过滤（AionsHome NotificationContextPolicy 移植）：广告/系统/ongoing运行中不缓存
            if (isNoise(category, ongoing, title, text)) {
                Log.d(TAG, "✂️ 过滤噪声通知 [" + pkg + "] cat=" + category);
                return;
            }

            // ── 音乐解析（仅音乐 App）──
            if (!isMusicPackage(pkg)) return;
            long songAge = System.currentTimeMillis() - sbn.getPostTime();
            if (sbn.getPostTime() <= 0 || songAge < -60_000L
                    || songAge > NotificationSnapshotCache.FRESH_WINDOW_MS) return;
            if (title.isEmpty()) return;

            // 1. 优先：title 里直接有 "歌名 - 歌手" 格式
            String[] parsed = parseSongTitle(title);
            if (parsed != null) {
                sSongTitle = parsed[0];
                sSongArtist = parsed[1];
                sSongTime = System.currentTimeMillis();
                Log.d(TAG, "🎵 [" + pkg + "] " + sSongTitle + " - " + sSongArtist + " (from title)");
                return;
            }

            // 2. 回退：title 只有歌名，尝试从 text 提取歌手
            //    text 格式可能是 "歌手 - 歌名" 或 "歌手 - 专辑名"，不可靠
            //    只用 text 的第一部分作歌手名，歌名始终用 title
            if (text != null && !text.isEmpty()) {
                sSongTitle = title;
                String[] textParsed = parseSongTitle(text);
                if (textParsed != null) {
                    sSongArtist = textParsed[0];  // text 左侧是歌手
                } else {
                    sSongArtist = "";  // 无法解析，留空让 enricher 用 pyncm 搜
                }
                sSongTime = System.currentTimeMillis();
                Log.d(TAG, "🎵 [" + pkg + "] " + sSongTitle + " - " + sSongArtist + " (title+text)");
                return;
            }

            // 3. 兜底：只有歌名，没有歌手（enricher 会用 pyncm 搜索补全）
            sSongTitle = title;
            sSongArtist = "";
            sSongTime = System.currentTimeMillis();
            Log.d(TAG, "🎵 [" + pkg + "] " + sSongTitle + " (artist unknown)");
        } catch (Exception e) {
            Log.w(TAG, "通知解析失败: " + e.getMessage());
        }
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification sbn) {
        if (sbn == null) return;
        String pkg = sbn.getPackageName();
        if (pkg == null) return;
        synchronized (sNotifLock) {
            sNotifications.remove(sbn.getKey());
        }
        notifyChanged();
    }

    @Override
    public void onListenerConnected() {
        super.onListenerConnected();
        sListener = this;
        Log.i(TAG, "NotificationListener 已连接");
        // 主动扫描已有通知
        try {
            StatusBarNotification[] active = getActiveNotifications();
            if (active != null) {
                for (StatusBarNotification sbn : active) {
                    onNotificationPosted(sbn);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "启动扫描失败: " + e.getMessage());
        }
        notifyChanged();
        // 上报由 Capacitor JS 端通过 MobileTool.mobile_now_playing 完成
    }

    // 上报逻辑由 Capacitor JS 端通过 MobileTool.mobile_now_playing 完成
    // NotificationListenerService 只负责缓存通知数据

    @Override
    public void onListenerDisconnected() {
        sListener = null;
        synchronized (sNotifLock) { sNotifications.clear(); }
        notifyChanged();
        Log.w(TAG, "NotificationListener 已断开");
    }
}
