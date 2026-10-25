package com.mirrow.app;

import org.json.JSONObject;

/**
 * 手机姿态/运动/光线分类器 — 从 AionsHome DeviceContextClassifier 移植（2026-08-24）。
 *
 * 纯状态分类，不持有 Android 传感器/网络。传感器数据由 MirrowRelayService 喂入，
 * snapshot() 输出带 observed_at/since/confidence 的 slot，供后端判时效。
 */
public final class DeviceContextClassifier {
    private static final long STABLE_MS = 2_000L;   // 姿态/运动 2s 稳定判定
    private static final long HEARTBEAT_MS = 5 * 60_000L;

    // ── 单例：MirrowRelayService（后台常驻采集）与 MobileToolPlugin（前台读取）共享状态 ──
    private static volatile DeviceContextClassifier sInstance;

    public static DeviceContextClassifier getInstance() {
        if (sInstance == null) {
            synchronized (DeviceContextClassifier.class) {
                if (sInstance == null) sInstance = new DeviceContextClassifier();
            }
        }
        return sInstance;
    }

    private DeviceContextClassifier() {}

    private String posture = "unknown";
    private String postureCandidate = "";
    private long postureCandidateAtMs;
    private long postureSinceMs;
    private long postureObservedAtMs;

    private String motion = "unknown";
    private String motionCandidate = "";
    private long motionCandidateAtMs;
    private long motionSinceMs;
    private long motionObservedAtMs;

    private String light = "unknown";
    private long lightSinceMs;
    private long lightObservedAtMs;
    private String proximity = "unknown";
    private long proximitySinceMs;
    private long proximityObservedAtMs;
    private String screen = "unknown";
    private long screenSinceMs;
    private long screenObservedAtMs;
    private String foregroundApp = "";
    private long foregroundAppSinceMs;
    private long foregroundAppObservedAtMs;

    private boolean dirty;
    private long lastEmittedAtMs;

    public synchronized void updateGravity(float x, float y, float z, long nowMs) {
        String next;
        float ax = Math.abs(x), ay = Math.abs(y), az = Math.abs(z);
        if (az >= 7f) {
            next = z < 0 ? "face_down" : "face_up";
        } else if (ax >= 5f && ax > ay) {
            next = x > 0 ? "landscape_left" : "landscape_right";
        } else if (ay >= 5f) {
            next = y > 0 ? "portrait" : "portrait_upside_down";
        } else {
            next = "tilted";
        }
        postureObservedAtMs = nowMs;
        if (next.equals(posture)) return;
        if (!next.equals(postureCandidate)) {
            postureCandidate = next;
            postureCandidateAtMs = nowMs;
            return;
        }
        if (nowMs - postureCandidateAtMs >= STABLE_MS) {
            posture = next;
            postureSinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized void updateMotion(float magnitude, long nowMs) {
        String next = magnitude < 0.18f ? "still"
                : magnitude < 1.2f ? "slight"
                : magnitude < 3.0f ? "moving" : "strong";
        motionObservedAtMs = nowMs;
        if (next.equals(motion)) return;
        if (!next.equals(motionCandidate)) {
            motionCandidate = next;
            motionCandidateAtMs = nowMs;
            return;
        }
        if (nowMs - motionCandidateAtMs >= STABLE_MS) {
            motion = next;
            motionSinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized void updateLight(float lux, long nowMs) {
        String next = lux < 8f ? "dark" : lux < 80f ? "dim" : lux < 350f ? "normal" : "bright";
        lightObservedAtMs = nowMs;
        if (!next.equals(light)) {
            light = next;
            lightSinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized void updateProximity(float distance, float maximumRange, long nowMs) {
        String next = distance < Math.min(5f, maximumRange) ? "near" : "far";
        proximityObservedAtMs = nowMs;
        if (!next.equals(proximity)) {
            proximity = next;
            proximitySinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized void updateScreen(boolean on, long nowMs) {
        String next = on ? "on" : "off";
        screenObservedAtMs = nowMs;
        if (!next.equals(screen)) {
            screen = next;
            screenSinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized void updateForegroundApp(String packageName, long nowMs) {
        String next = packageName == null ? "" : packageName.trim();
        if (next.isEmpty()) return;
        foregroundAppObservedAtMs = nowMs;
        if (!next.equals(foregroundApp)) {
            foregroundApp = next;
            foregroundAppSinceMs = nowMs;
            dirty = true;
        }
    }

    public synchronized JSONObject snapshot(long nowMs) {
        JSONObject result = new JSONObject();
        putSlot(result, "posture", posture, postureObservedAtMs, postureSinceMs, 0.9);
        putSlot(result, "motion", motion, motionObservedAtMs, motionSinceMs, 0.8);
        putSlot(result, "light", light, lightObservedAtMs, lightSinceMs, 0.95);
        putSlot(result, "proximity", proximity, proximityObservedAtMs, proximitySinceMs, 0.95);
        putSlot(result, "screen", screen, screenObservedAtMs, screenSinceMs, 1.0);
        putSlot(result, "foreground_app", foregroundApp, foregroundAppObservedAtMs,
                foregroundAppSinceMs, 0.85);
        return result;
    }

    private static void putSlot(JSONObject target, String key, String value,
                                long observedAtMs, long sinceMs, double confidence) {
        if (value == null || value.isEmpty() || "unknown".equals(value) || observedAtMs <= 0) return;
        try {
            JSONObject slot = new JSONObject();
            slot.put("value", value);
            slot.put("observed_at", observedAtMs / 1000.0);
            slot.put("since", sinceMs / 1000.0);
            slot.put("confidence", confidence);
            target.put(key, slot);
        } catch (Exception ignored) {
            // 原始值 JSON 安全，此处仅兼容签名
        }
    }

    public synchronized boolean shouldEmit(long nowMs) {
        return dirty || nowMs - lastEmittedAtMs >= HEARTBEAT_MS;
    }

    public synchronized void markEmitted(long nowMs) {
        dirty = false;
        lastEmittedAtMs = nowMs;
    }
}
