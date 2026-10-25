package com.mirrow.app;

/**
 * Keeps the content application separate from temporary overlay windows such as an IME.
 *
 * <p>An input method is not a standalone user activity: it is normally shown on top of
 * another application.  Replacing the content package with the IME package loses the
 * identity of that host application, so callers should use {@link #getContentPackage()} as
 * the effective foreground app and expose {@link #getObservedWindowPackage()} only as
 * provenance.</p>
 */
public final class ForegroundWindowState {
    private String contentPackage = "";
    private String overlayPackage = "";
    private String observedWindowPackage = "";
    private long contentObservedAtMs = 0L;
    private long observedAtMs = 0L;

    public synchronized void observe(String packageName, boolean inputMethod, long observedAtMs) {
        if (packageName == null || packageName.trim().isEmpty()) return;
        String pkg = packageName.trim();
        this.observedWindowPackage = pkg;
        this.observedAtMs = observedAtMs;
        if (inputMethod) {
            this.overlayPackage = pkg;
            return;
        }
        this.contentPackage = pkg;
        this.contentObservedAtMs = observedAtMs;
        this.overlayPackage = "";
    }

    public synchronized void clear() {
        contentPackage = "";
        overlayPackage = "";
        observedWindowPackage = "";
        contentObservedAtMs = 0L;
        observedAtMs = 0L;
    }

    public synchronized String getContentPackage() {
        return contentPackage;
    }

    public synchronized String getOverlayPackage() {
        return overlayPackage;
    }

    public synchronized String getObservedWindowPackage() {
        return observedWindowPackage;
    }

    public synchronized long getContentObservedAtMs() {
        return contentObservedAtMs;
    }

    public synchronized long getObservedAtMs() {
        return observedAtMs;
    }

    public synchronized boolean isObscuredByInputMethod() {
        return !overlayPackage.isEmpty();
    }
}
