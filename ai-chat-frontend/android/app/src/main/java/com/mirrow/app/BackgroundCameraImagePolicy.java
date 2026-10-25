package com.mirrow.app;

/** Pure image sizing policy, kept Android-free so it can be JVM-tested. */
public final class BackgroundCameraImagePolicy {
    public static final int MAX_LONG_EDGE = 1280;
    public static final int MAX_JPEG_BYTES = 800 * 1024;
    public static final int INITIAL_JPEG_QUALITY = 80;
    public static final int MIN_JPEG_QUALITY = 55;

    private BackgroundCameraImagePolicy() {}

    public static String normalizeFacing(String facing) {
        return ("front".equalsIgnoreCase(facing) || "user".equalsIgnoreCase(facing))
                ? "front" : "back";
    }

    public static int[] targetDimensions(int width, int height) {
        if (width <= 0 || height <= 0) return new int[]{0, 0};
        int longest = Math.max(width, height);
        if (longest <= MAX_LONG_EDGE) return new int[]{width, height};
        float scale = MAX_LONG_EDGE / (float) longest;
        return new int[]{Math.max(1, Math.round(width * scale)),
                Math.max(1, Math.round(height * scale))};
    }

    public static int nextQuality(int current) {
        return current <= MIN_JPEG_QUALITY ? MIN_JPEG_QUALITY
                : Math.max(MIN_JPEG_QUALITY, current - 5);
    }
}
