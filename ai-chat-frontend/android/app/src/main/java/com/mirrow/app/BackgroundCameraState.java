package com.mirrow.app;

/**
 * Small process-local state machine for the native one-shot camera.
 *
 * The persisted preference records that the dedicated MIRROW device may use
 * the feature.  It deliberately does not restore {@code processArmed} after
 * a process restart: Android's while-in-use camera permission requires a new
 * visible-activity arm before the relay advertises this capability again.
 */
public final class BackgroundCameraState {
    private final boolean userEnabled;
    private boolean processArmed;

    public BackgroundCameraState(boolean userEnabled) {
        this.userEnabled = userEnabled;
    }

    public synchronized boolean armFromVisibleActivity(boolean cameraPermissionGranted,
                                                        boolean controllerAvailable,
                                                        boolean foregroundUpgradeSucceeded) {
        processArmed = userEnabled && cameraPermissionGranted
                && controllerAvailable && foregroundUpgradeSucceeded;
        return processArmed;
    }

    public synchronized void disarm() {
        processArmed = false;
    }

    public synchronized boolean isReady(boolean cameraPermissionGranted,
                                        boolean controllerAvailable) {
        if (!cameraPermissionGranted || !controllerAvailable) {
            processArmed = false;
        }
        return processArmed;
    }

    public boolean isUserEnabled() {
        return userEnabled;
    }
}
