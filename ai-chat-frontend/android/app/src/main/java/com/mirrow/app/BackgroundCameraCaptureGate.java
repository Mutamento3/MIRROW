package com.mirrow.app;

/** Android-free generation gate: stale camera callbacks may never own a new capture. */
public final class BackgroundCameraCaptureGate {
    private long nextToken = 1L;
    private long activeToken = 0L;

    public synchronized long begin() {
        if (activeToken != 0L) return -1L;
        activeToken = nextToken++;
        return activeToken;
    }

    public synchronized boolean isCurrent(long token) {
        return token != 0L && activeToken == token;
    }

    public synchronized void complete(long token) {
        if (activeToken == token) activeToken = 0L;
    }

    /** Invalidates all callbacks for the returned in-flight token. */
    public synchronized long cancel() {
        long cancelled = activeToken;
        activeToken = 0L;
        return cancelled;
    }
}
